# -*- coding: utf-8 -*-
"""v0.9.113.0 — STAIR heading metadata tests (ROADMAP item 2)."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, ".")

import src.ingest.headings as hd


# ── 1. headingSections ───────────────────────────────────────────────
class TestHeadingSections:
    def test_path_accumulation(self):
        secs = hd.heading_sections("# A\nbody a\n## B\ndeep\n# C\nbody c")
        assert [s["path"] for s in secs] == ["A", "A > B", "C"]
        assert [s["level"] for s in secs][1] == 2

    def test_preamble_before_first_heading(self):
        secs = hd.heading_sections("intro text\n# A\nbody")
        assert secs[0]["path"] == "" and "intro text" in secs[0]["text"]
        assert secs[1]["path"] == "A"

    def test_no_heading_single_section(self):
        secs = hd.heading_sections("plain\nlines\nonly")
        assert len(secs) == 1 and secs[0]["path"] == ""
        assert secs[0]["text"] == "plain\nlines\nonly"

    def test_empty_text(self):
        assert hd.heading_sections("") == [
            {"path": "", "level": 0, "title": "", "text": ""}]

    def test_six_levels_and_closing_hashes(self):
        secs = hd.heading_sections("###### deep ######\nx")
        assert secs[0]["path"] == "deep" and secs[0]["level"] == 6

    def test_pptx_slide_heading_shape(self):
        secs = hd.heading_sections(
            "# Slide 1: 標題\n- bullet\n# Slide 2: 第二頁\n- b2")
        assert [s["path"] for s in secs] == [
            "Slide 1: 標題", "Slide 2: 第二頁"]

    def test_cjk_titles(self):
        secs = hd.heading_sections("## 第一章 總論\n內文\n### 1.2 範圍\nx")
        assert secs[1]["path"] == "第一章 總論 > 1.2 範圍"

    def test_skips_inline_hashes_and_lists(self):
        secs = hd.heading_sections("code ### not heading\n# H\n- # item\n## H2")
        # the `code ### not heading` line is text BEFORE the first heading
        # (preamble section); `- # item` list line must NOT open a section.
        assert [s["path"] for s in secs] == ["", "H", "H > H2"]

    def test_has_headings(self):
        assert hd.has_headings("# x") is True
        assert hd.has_headings("plain ## text") is False

    def test_heading_for_text_last_wins(self):
        p, t = hd.heading_for_text("body\n## Sub\nmore\n## Tail\ntext")
        assert (p, t) == ("Tail", "Tail")

    def test_heading_for_text_inherits_last_path(self):
        assert hd.heading_for_text("no heading here", "Prev > Path") == (
            "Prev > Path", "")


# ── 2. Chunker heading-aware path ────────────────────────────────────
class TestChunkDocumentWithHeadings:
    def _ch(self):
        from src.ingest.chunker import TextChunker
        return TextChunker(chunk_size=80, chunk_overlap=10)

    def test_chunks_inherit_heading_path(self):
        text = "# Intro\n" + ("alpha body. " * 20) + "\n## Next\n" + (
            "beta body. " * 20)
        chunks = self._ch().chunk_document_with_headings(text, "x/f.md")
        assert chunks, "should produce chunks"
        first = [c for c in chunks if c.metadata.get("heading_path")]
        assert first[0].metadata["heading_path"] == "Intro"
        assert any(c.metadata.get("heading_path") == "Intro > Next"
                   for c in chunks)

    def test_index_and_total_are_document_wide(self):
        text = "# A\n" + ("x. " * 60) + "\n# B\n" + ("y. " * 60)
        chunks = self._ch().chunk_document_with_headings(text, "f")
        idx = [c.metadata["chunk_index"] for c in chunks]
        assert idx == sorted(idx) and len(set(idx)) == len(idx)
        assert all(c.metadata["total_chunks"] == len(chunks) for c in chunks)

    def test_no_heading_falls_back_to_plain(self):
        from src.ingest.chunker import TextChunker
        ch = self._ch()
        text = "plain text without any headings at all. " * 30
        got = ch.chunk_document_with_headings(text, "f")
        want = ch.chunk_document(text, "f")
        assert [c.text for c in got] == [c.text for c in want]
        assert got[0].metadata["chunk_index"] == want[0].metadata["chunk_index"]

    def test_base_metadata_passes_through(self):
        text = "# T\nbody"
        chunks = self._ch().chunk_document_with_headings(
            text, "p/f.md", {"filename": "f.md", "extension": ".md"})
        assert chunks[0].metadata["filename"] == "f.md"
        assert chunks[0].metadata["source"] == "p/f.md"

    def test_preamble_chunk_has_no_heading_key(self):
        text = "freestanding preamble paragraph.\n\n# A\nbody"
        chunks = self._ch().chunk_document_with_headings(text, "f")
        assert "heading_path" not in chunks[0].metadata
        assert any("heading_path" in c.metadata for c in chunks[1:])


# ── 3. Loader DOCX heading prefix ────────────────────────────────────
class TestLoaderHeadingPrefix:
    def _para(self, style_name):
        return types.SimpleNamespace(style=types.SimpleNamespace(name=style_name))

    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("QDRANT_HEADING_METADATA", raising=False)
        from src.ingest.loader import _docx_heading_prefix
        assert _docx_heading_prefix(self._para("Heading 1")) == ""

    def test_on_produces_atx(self, monkeypatch):
        monkeypatch.setenv("QDRANT_HEADING_METADATA", "on")
        from src.ingest.loader import _docx_heading_prefix
        assert _docx_heading_prefix(self._para("Heading 1")) == "# "
        assert _docx_heading_prefix(self._para("Heading 3")) == "### "
        assert _docx_heading_prefix(self._para("標題 2")) == "## "

    def test_plain_style_or_bad_names(self, monkeypatch):
        monkeypatch.setenv("QDRANT_HEADING_METADATA", "on")
        from src.ingest.loader import _docx_heading_prefix
        assert _docx_heading_prefix(self._para("Normal")) == ""
        assert _docx_heading_prefix(self._para("Heading 9")) == ""
        assert _docx_heading_prefix(self._para(None)) == ""


# ── 4. Pipeline payload field ────────────────────────────────────────
class TestPipelineHeadingPayload:
    def _run(self, texts, meta=None):
        from src.ingest.pipeline import IngestPipeline
        p = IngestPipeline.__new__(IngestPipeline)
        p.embedder = types.SimpleNamespace(
            embed_batch=lambda ts: [[0.1] * 4 for _ in ts],
            last_backends={},
        )
        chunks = [types.SimpleNamespace(text=t, metadata=dict(meta or {}))
                  for t in texts]
        calls = []
        p.store = types.SimpleNamespace(upsert=lambda pts: calls.extend(pts))
        p._embed_and_upsert(Path("x/f.md"), chunks, ["h"] * len(texts),
                            "C:/x/f.md")
        return calls

    def test_heading_path_in_payload(self):
        calls = self._run(["a"], {"heading_path": "Intro > Setup"})
        assert calls[0]["payload"]["heading_path"] == "Intro > Setup"

    def test_off_path_ingest_writes_empty(self):
        calls = self._run(["a"], {})
        assert calls[0]["payload"]["heading_path"] == ""


# ── 5. Retriever heading boost ───────────────────────────────────────
class TestHeadingBoost:
    def _rt(self):
        from src.vector_store.retriever import Retriever
        return Retriever.__new__(Retriever)

    def _hits(self):
        return [
            {"id": "a", "score": 0.5, "payload": {"heading_path": "FinRL > Env"}},
            {"id": "b", "score": 0.44, "payload": {"heading_path": ""}},
            {"id": "c", "score": 0.4, "payload": {}},
        ]

    def test_off_is_noop(self, monkeypatch):
        monkeypatch.delenv("QDRANT_HEADING_BOOST", raising=False)
        r = self._rt()
        hits = self._hits()
        assert r.apply_heading_boost("finrl", hits) is hits
        assert hits[0]["id"] == "a"

    def test_bad_value_is_off(self, monkeypatch):
        monkeypatch.setenv("QDRANT_HEADING_BOOST", "abc")
        assert self._rt().heading_boost_value() == 0.0

    def test_on_resorts_and_tags(self, monkeypatch):
        monkeypatch.setenv("QDRANT_HEADING_BOOST", "0.1")
        r = self._rt()
        hits = self._hits()
        out = r.apply_heading_boost("finrl 環境", hits)
        assert out[0]["id"] == "a"          # bumped over b
        assert out[0]["heading_boost"] == 0.1
        assert out[1]["id"] == "b"
        assert out[1]["heading_boost"] == 0.0

    def test_overlap_is_bidirectional_substring(self):
        r = self._rt()
        assert r._heading_overlap("FinRL > Env", "what is FinRL > Env") is True
        assert r._heading_overlap("FinRL > Env", "FINRL") is True
        assert r._heading_overlap("", "q") is False
        assert r._heading_overlap("x", "") is False
        assert r._heading_overlap("Deep > Dive", "unrelated") is False

    def test_boost_default_zero(self, monkeypatch):
        monkeypatch.delenv("QDRANT_HEADING_BOOST", raising=False)
        assert self._rt().heading_boost_value() == 0.0


# ── 6. Backfill walk ─────────────────────────────────────────────────
class _ScrollClient:
    """Mimics store.client for the backfill walk (scroll/set_payload/index)."""

    def __init__(self, rows):
        self._rows = rows
        self.set_calls = []
        self.index_calls = []

    def scroll(self, collection_name, limit=500, offset=None, **_):
        start = offset or 0

        class _P:
            def __init__(self, row):
                self.id = row["id"]
                self.payload = row["payload"]

        page = self._rows[start:start + limit]
        nxt = (start + len(page)
               if start + len(page) < len(self._rows) else None)
        return [_P(r) for r in page], nxt

    def create_payload_index(self, **k):
        self.index_calls.append(k)

    def set_payload(self, **k):
        self.set_calls.append(k)


def _rows_def():
    return [
        {"id": "p1", "payload": {"source": "C:/a.md", "chunk_index": 0,
                                 "text": "intro paragraph, no heading"}},
        {"id": "p2", "payload": {"source": "C:/a.md", "chunk_index": 1,
                                 "text": "## A 開頭\nbody under A"}},
        {"id": "p3", "payload": {"source": "C:/a.md", "chunk_index": 2,
                                 "text": "carried body, no heading"}},
        {"id": "p4", "payload": {"source": "C:/a.md", "chunk_index": 3,
                                 "text": "already tagged",
                                 "heading_path": "Kept"}},
    ]


class TestBackfill:
    def _run(self, rows, **kw):
        from src.ingest import heading_backfill as hb

        store = types.SimpleNamespace(collection="coll")
        client = _ScrollClient(rows)
        store.client = client
        report = hb.backfill_collection(store, log=None, **kw)
        return report, client

    def test_fill_walk_and_carry_over(self):
        report, client = self._run(_rows_def())
        assert report["scanned"] == 4
        assert report["updated"] == 2  # p2 + p3 (p1 none; p4 kept)
        by_id = {}
        for call in client.set_calls:
            for pid in call["points"]:
                by_id[pid] = call["payload"]["heading_path"]
        assert by_id["p2"] == "A 開頭"
        assert by_id["p3"] == "A 開頭"

    def test_existing_honoured_without_force(self):
        report, client = self._run(_rows_def())
        touched = [p for c in client.set_calls for p in c["points"]]
        assert "p4" not in touched

    def test_payload_index_requested(self):
        _, client = self._run(_rows_def())
        assert client.index_calls[0]["field_name"] == "heading_path"


def _paths_written(client):
    by_id = {}
    for call in client.set_calls:
        for pid in call["points"]:
            by_id[pid] = call["payload"]["heading_path"]
    return by_id


class TestBackfillMore:
    def _run(self, rows, **kw):
        from src.ingest import heading_backfill as hb

        store = types.SimpleNamespace(collection="coll")
        client = _ScrollClient(rows)
        store.client = client
        report = hb.backfill_collection(store, log=None, **kw)
        return report, client

    def test_force_recomputes_existing(self):
        report, client = self._run(_rows_def(), force=True)
        assert "p4" in _paths_written(client)

    def test_limit_caps_targets(self):
        report, _ = self._run(_rows_def(), limit=1)
        assert report["updated"] == 1

    def test_sources_counter(self):
        report, _ = self._run(_rows_def())
        assert report["sources"] == 1

    def test_multisource_resets_last(self):
        rows = [
            {"id": "a1", "payload": {"source": "C:/1.md", "chunk_index": 0,
                                     "text": "# One\nbody"}},
            {"id": "a2", "payload": {"source": "C:/1.md", "chunk_index": 1,
                                     "text": "carried"}},
            {"id": "b1", "payload": {"source": "C:/2.md", "chunk_index": 0,
                                     "text": "bare start"}},
        ]
        report, client = self._run(rows)
        assert report["sources"] == 2
        assert report["updated"] == 2  # a1 + a2; b1 has no heading anywhere
        assert _paths_written(client) == {"a1": "One", "a2": "One"}

    def test_textless_rows_skipped_never_raises(self):
        report, _ = self._run(
            [{"id": "z", "payload": {"source": "x", "chunk_index": 0,
                                     "text": None}}])
        assert report["scanned"] == 0
        assert report["updated"] == 0

    def test_set_payload_fail_counts_error(self):
        from src.ingest import heading_backfill as hb

        store = types.SimpleNamespace(collection="c")
        client = _ScrollClient(_rows_def())
        store.client = client

        def boom(**k):
            raise RuntimeError("qdrant down")
        client.set_payload = boom
        report = hb.backfill_collection(store, log=None)
        assert report["updated"] == 0
        assert report["errors"] >= 1

    def test_index_fail_is_best_effort(self):
        from src.ingest import heading_backfill as hb

        store = types.SimpleNamespace(collection="c")
        client = _ScrollClient(_rows_def())
        store.client = client

        def idx_boom(**k):
            raise RuntimeError("index failed")
        client.create_payload_index = idx_boom
        report = hb.backfill_collection(store, log=None)
        assert report["updated"] == 2
        assert report["heading_index"] is False