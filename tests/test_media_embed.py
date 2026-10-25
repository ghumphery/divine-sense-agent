# -*- coding: utf-8 -*-
"""v0.9.114.0 — media side-channel (EmbeddingGemma 2) tests."""
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, ".")

import src.ingest.media_embed as me


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.ok = status < 400

    def raise_for_status(self):
        self._body_exc = None

    def json(self):
        return self._body


class _Store:
    def __init__(self, collection="m"):
        self.collection = collection
        self.upserts = []

    def upsert(self, pts):
        self.upserts.extend(pts)


@pytest.fixture(autouse=True)
def _cooled():
    me.reset_media_probe()
    yield
    me.reset_media_probe()


class TestGatesAndUrls:
    def test_gates_default_off(self, monkeypatch):
        monkeypatch.delenv("QDRANT_MEDIA_EMBEDDING", raising=False)
        monkeypatch.delenv("QDRANT_MEDIA_INGEST", raising=False)
        assert me.media_embedding_enabled() is False
        assert me.media_ingest_files_enabled() is False

    def test_gates_on_aliases(self, monkeypatch):
        for v in ("on", "1", "true", "ON", "Yes"):
            monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", v)
            assert me.media_embedding_enabled() is True

    def test_default_urls(self):
        assert me.media_collection() == "media_docs_gemma2"
        assert me.media_embed_url() == "http://127.0.0.1:8081/v1/embeddings"

    def test_media_embed_url_localhost_rewrite(self, monkeypatch):
        monkeypatch.setenv("GEMMA2_EMBED_URL",
                           "http://localhost:8081/v1/embeddings")
        assert me.media_embed_url() == "http://127.0.0.1:8081/v1/embeddings"


class TestSanityProbe:
    def test_fake_success_detected(self, monkeypatch):
        # ggml-org#30082: DIFFERENT images both return the text-only
        # marker vector -> cos == 1.0 -> judged unavailable.
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        row = [0.1] * 8
        with mock.patch.object(
                me, "_post", return_value=_Resp(200, {"embedding": row})):
            ok, note = me.media_path_available(force=True)
        assert ok is False
        assert "FAKESUCCESS" in note

    def test_real_media_passes(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        rows = [[1.0] + [0.0] * 7, [0.0, 1.0] + [0.0] * 6]
        with mock.patch.object(me, "_post", side_effect=[
                _Resp(200, {"embedding": rows[0]}),
                _Resp(200, {"embedding": rows[1]})]):
            ok, note = me.media_path_available(force=True)
        assert ok is True
        assert "REAL" in note

    def test_verdict_cached(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        with mock.patch.object(
                me, "_post",
                return_value=_Resp(200, {"embedding": [0.1] * 8})):
            me.media_path_available(force=True)
        with mock.patch.object(me, "_post",
                               side_effect=AssertionError("no")):
            ok, _ = me.media_path_available()
        assert ok is False  # cached verdict, no re-probe

    def test_endpoint_error_is_unavailable(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        with mock.patch.object(me, "_post",
                               side_effect=ConnectionError("refused")):
            ok, _ = me.media_path_available(force=True)
        assert ok is False

    def test_pillow_missing(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        with mock.patch.dict(sys.modules,
                             {"PIL": None, "PIL.Image": None}):
            ok, note = me.media_path_available(force=True)
        assert ok is False and "Pillow" in note


class TestEmbedMediaVector:
    def test_none_when_unavailable(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        with mock.patch.object(
                me, "_post",
                return_value=_Resp(200, {"embedding": [0.1] * 8})):
            me.media_path_available(force=True)  # verdict unavailable
        with mock.patch.object(me, "_post",
                               side_effect=AssertionError("no")):
            assert me.embed_media_vector("x.png") is None
            assert me.embed_media_vector("x.png", "audio") is None

    def test_real_image_returns_row(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (16, 16), (9, 120, 9)).save(buf, format="PNG")
        path = tmp_path / "im.png"
        path.write_bytes(buf.getvalue())
        with mock.patch.object(me, "_post", side_effect=[
                _Resp(200, {"embedding": [1.0] + [0.0] * 7}),
                _Resp(200, {"embedding": [0.0, 1.0] + [0.0] * 6}),
                _Resp(200, {"embedding": [0.2, 0.8] + [0.0] * 6}),
        ]) as posts:
            vec = me.embed_media_vector(str(path))
        assert vec and len(vec) == 8
        assert posts.call_count == 3  # probe×2 + embed×1

    def test_missing_file_is_none(self, monkeypatch):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        with mock.patch.object(me, "media_path_available",
                               return_value=(True, "real")):
            assert me.embed_media_vector("nope.png") is None

    def test_nested_content_shape_first_v0122_1(self, monkeypatch, tmp_path):
        # v0.9.122.1 — ggml-org#30082 was RETRACTED by its author: image
        # embeddings are wired since llama.cpp #30054 (builds b11457+)
        # via the NESTED content-parts shape. The FIRST POST must use
        # that shape, and NO legacy fallback may fire when it succeeds
        # (the legacy shapes silently return text-only vectors).
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (8, 8), (9, 120, 9)).save(buf, format="PNG")
        png = tmp_path / "x.png"
        png.write_bytes(buf.getvalue())
        jpg = tmp_path / "p.jpg"
        jpg.write_bytes(b"\xff\xd8fakejpeg")
        with mock.patch.object(me, "media_path_available",
                               return_value=(True, "real")):
            with mock.patch.object(
                    me, "_post",
                    return_value=_Resp(
                        200, {"data": [{"embedding": [1.0] * 8}]})) as posts:
                vec = me.embed_media_vector(str(png))
            assert vec == [1.0] * 8
            assert posts.call_count == 1
            assert posts.call_args.args[0].endswith("/v1/embeddings")
            part = posts.call_args.args[1]["input"][0]["content"][0]
            assert part["type"] == "image_url"
            assert part["image_url"]["url"].startswith(
                "data:image/png;base64,")
            # .jpg maps to the correct mime; "embeddings" envelope row too
            with mock.patch.object(
                    me, "_post",
                    return_value=_Resp(200, {"embeddings": [[0.5] * 8]})):
                assert me.embed_media_vector(str(jpg)) == [0.5] * 8

    def test_audio_nested_then_legacy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_MEDIA_EMBEDDING", "on")
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        with mock.patch.object(me, "media_path_available",
                               return_value=(True, "real")):
            with mock.patch.object(me, "_post", side_effect=[
                    _Resp(400, {}),
                    _Resp(200, {"embedding": [9.0]})]) as posts:
                vec = me.embed_media_vector(str(wav), "audio")
            assert vec == [9.0]
            assert posts.call_count == 2
            part = posts.call_args_list[0].args[1]["input"][0]["content"][0]
            assert part["type"] == "input_audio"
            assert part["input_audio"]["format"] == "wav"
            assert posts.call_args_list[1].args[0].endswith("/embedding")


class TestIndexMediaBatch:
    def test_gate_off_is_noop(self, monkeypatch):
        monkeypatch.delenv("QDRANT_MEDIA_EMBEDDING", raising=False)
        rep = me.index_media_batch(_Store(), [{"path": "x"}])
        assert rep == {"indexed": 0, "unavailable": 0, "errors": 0}

    def test_unavailable_returns_honest(self, monkeypatch):
        monkeypatch.setattr(me, "media_embedding_enabled", lambda: True)
        with mock.patch.object(me, "media_path_available",
                               return_value=(False, "fakesuccess")):
            rep = me.index_media_batch(_Store(), [{"path": "x"}])
        assert rep["indexed"] == 0 and rep["unavailable"] == 1
        assert "fakesuccess" in rep["note"]

    def test_indexes_with_side_payload(self, monkeypatch, tmp_path):
        monkeypatch.setattr(me, "media_embedding_enabled", lambda: True)
        st = _Store(collection="media_docs_gemma2")
        with mock.patch.object(me, "media_path_available",
                               return_value=(True, "real")), \
             mock.patch.object(me, "embed_media_vector",
                               return_value=[0.3, 0.4] + [0.0] * 6):
            rep = me.index_media_batch(st, [{
                "path": str(tmp_path / "im.png"), "kind": "image",
                "source": "C:/d.docx", "filename": "d.docx",
                "note": "a chart of losses", "position": "media #1"}])
        assert rep["indexed"] == 1
        pt = st.upserts[0]
        assert pt["payload"]["embedding_backend"] == "embeddinggemma2-image"
        assert pt["payload"]["media_kind"] == "image"
        assert pt["payload"]["text"].startswith("a chart")
        assert pt["vector"][0] > 0 and len(pt["vector"]) == 8


class TestPipelineWiring:
    def test_media_exts_split(self):
        from src.ingest.pipeline import IngestPipeline
        assert ".png" in IngestPipeline._MEDIA_IMAGE_EXTS
        assert ".mp3" in IngestPipeline._MEDIA_AV_EXTS
        assert ".md" not in IngestPipeline._MEDIA_IMAGE_EXTS

    def test_media_file_gate_off_skips(self, monkeypatch, tmp_path,
                                        capsys):
        from src.ingest import pipeline as pl
        monkeypatch.delenv("QDRANT_MEDIA_INGEST", raising=False)
        p = tmp_path / "im.png"
        p.write_bytes(b"x")
        pipe = pl.IngestPipeline.__new__(pl.IngestPipeline)
        assert pipe.ingest_file(str(p)) == 0
        assert "QDRANT_MEDIA_INGEST" in capsys.readouterr().out

    def test_av_ext_skipped_with_note(self, tmp_path, capsys):
        from src.ingest import pipeline as pl
        p = tmp_path / "a.mp3"
        p.write_bytes(b"x")
        pipe = pl.IngestPipeline.__new__(pl.IngestPipeline)
        assert pipe.ingest_file(str(p)) == 0
        assert "pending upstream" in capsys.readouterr().out

    def test_side_car_hook_present(self):
        src = open("src/ingest/pipeline.py", encoding="utf-8").read()
        assert "index_media_batch" in src
        assert "_media_embedding_enabled" in src
        assert "_ingest_media_file" in src


class TestSearchMediaTool:
    def _host(self):
        from src.mcp_client.host import QdrantLocalHost
        with mock.patch.object(QdrantLocalHost, "__init__",
                               lambda self: None):
            return QdrantLocalHost()

    def test_tool_registered(self):
        h = self._host()
        tools = h.list_tools()
        assert "search_media" in [t.name for t in tools]
        t = next(t for t in tools if t.name == "search_media")
        assert t.server_name == "qdrant-local"
        assert "query" in (t.input_schema or {}).get("properties", {})

    def test_disabled_is_honest(self, monkeypatch):
        h = self._host()
        monkeypatch.setattr(me, "media_embedding_enabled", lambda: False)
        r = h._search_media("chart of losses")
        assert "error" in r and "search_documents" in r["error"]

    def test_unavailable_is_honest(self, monkeypatch):
        h = self._host()
        monkeypatch.setattr(me, "media_embedding_enabled", lambda: True)
        monkeypatch.setattr(
            me, "media_path_available", lambda *_a: (False, "cos=1.0 fake"))
        r = h._search_media("q")
        assert "error" in r and "fake" in r["error"]

    def test_search_hits_side_collection(self, monkeypatch):
        h = self._host()
        monkeypatch.setattr(me, "media_embedding_enabled", lambda: True)
        monkeypatch.setattr(me, "media_path_available",
                            lambda *_a: (True, "real"))
        monkeypatch.setattr(me, "_embed_texts_server",
                            lambda ts: [[0.1] * 8 for _ in ts])
        side = _Store(collection="media_docs_gemma2")
        side.search = lambda **kw: [
            {"score": 0.9, "filename": "d.docx", "text": "desc",
             "payload": {"media_name": "im.png", "media_kind": "image"}}]
        with mock.patch("src.vector_store.qdrant_client.QdrantStore",
                        return_value=side):
            r = h._search_media("chart", top_k=3)
        assert r["count"] == 1
        assert r["backend"].startswith("embeddinggemma2")
        assert r["results"][0]["media_name"] == "im.png"

    def test_embed_error_is_honest(self, monkeypatch):
        h = self._host()
        monkeypatch.setattr(me, "media_embedding_enabled", lambda: True)
        monkeypatch.setattr(me, "media_path_available",
                            lambda *_a: (True, "real"))
        monkeypatch.setattr(me, "_embed_texts_server",
                            mock.Mock(side_effect=ConnectionError("down")))
        r = h._search_media("chart")
        assert "error" in r and "embed failed" in r["error"]


def test_stdio_server_has_search_media():
    src = open("src/mcp_server/server.py", encoding="utf-8").read()
    assert '"search_media"' in src
    assert "_do_search_media" in src
    assert src.count('"search_media"') >= 2  # tool list + dispatch