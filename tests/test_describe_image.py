"""Tests for v0.9.1 QdrantLocalHost.describe_image.

Covers the new LLM-driven vision tool exposed on the
``qdrant-local`` server. Two modes share the same handler:

* ``mode="path"``  — sandbox-resident file (the LLM points at
  a file already in ``QDRANT_IMPORT_ALLOWED_ROOT``).
* ``mode="base64"`` — inline bytes (LLM hands us a data URL
  or bare base64; we decode + write to a sandbox tmp file,
  call vision, clean up).

All vision-API network calls and the SHA-1 cache are
mocked — these tests are pure-Python, no Qdrant / Ollama
required.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.mcp_client.host import (
    QdrantLocalHost,
    _DESCRIBE_IMAGE_MAX_BYTES,
    _DESCRIBE_IMAGE_MIMES,
    _IMAGE_MAGIC,
    _ext_for_mime,
    _parse_data_url,
    _write_base64_to_sandbox_tmp,
)


# ── helpers ────────────────────────────────────────────────


# 1x1 transparent PNG (canonical Wikipedia test fixture).
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000"
    "001f15c4890000000d49444154789c63f8cf00000003000100"
    "b63e0c920000000049454e44ae426082"
)


def _png_b64() -> str:
    return base64.b64encode(PNG_BYTES).decode("ascii")


def _png_data_url() -> str:
    return "data:image/png;base64," + _png_b64()


def _make_host_with_mocks(tmp_path, existing_files=None):
    """Build a QdrantLocalHost with the QdrantStore / Retriever
    stubbed out so ``__init__`` doesn't reach for Qdrant."""
    host = QdrantLocalHost()
    host._store = MagicMock()
    host._store.list_documents.return_value = existing_files or []
    host._retriever = MagicMock()
    return host


def _fake_describer(
    description="A test image with a 1x1 transparent pixel.",
    side_effect=None,
    cache_state=None,
):
    """Build a stand-in for ``ImageDescriber`` that returns a
    canned description (or raises ``side_effect``). The ``PROMPT``
    attribute is left at class default unless ``custom_prompt`` is
    requested at the call site."""
    fake = MagicMock()
    fake.describe.return_value = description
    if side_effect is not None:
        fake.describe.side_effect = side_effect
    fake.PROMPT = "default bilingual prompt"
    fake._cache = cache_state if cache_state is not None else {}
    return fake


# ``_describe_image`` imports ``ImageDescriber`` inside its
# own body, so the patch must target the class object where
# it actually lives, not ``host.ImageDescriber``.
_PATCH_IMG_DESC = "src.ingest.image_describer.ImageDescriber"


# ── _ext_for_mime ──────────────────────────────────────────


class TestExtForMime:
    def test_known_mimes(self):
        assert _ext_for_mime("image/png") == ".png"
        assert _ext_for_mime("image/jpeg") == ".jpg"
        assert _ext_for_mime("image/gif") == ".gif"
        assert _ext_for_mime("image/webp") == ".webp"
        assert _ext_for_mime("image/bmp") == ".bmp"
        assert _ext_for_mime("image/svg+xml") == ".svg"

    def test_unknown_falls_back_to_bin(self):
        assert _ext_for_mime("image/xyz") == ".bin"
        assert _ext_for_mime("") == ".bin"


# ── _parse_data_url ────────────────────────────────────────


class TestParseDataUrl:
    def test_bare_base64_defaults_to_png(self):
        mime, decoded = _parse_data_url(_png_b64())
        assert mime == "image/png"
        assert decoded == PNG_BYTES

    def test_full_data_url(self):
        mime, decoded = _parse_data_url(_png_data_url())
        assert mime == "image/png"
        assert decoded == PNG_BYTES

    def test_jpeg_data_url(self):
        # Tiny fake JPEG with valid magic bytes.
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"
        url = "data:image/jpeg;base64," + base64.b64encode(
            fake_jpeg,
        ).decode("ascii")
        mime, decoded = _parse_data_url(url)
        assert mime == "image/jpeg"
        assert decoded == fake_jpeg

    def test_non_base64_data_url_rejected(self):
        with pytest.raises(ValueError, match="only base64"):
            _parse_data_url("data:image/png;utf8,blah")

    def test_unsupported_mime_rejected(self):
        # Use 4-byte input so base64 padding is valid; the mime
        # check is what we want to fail.
        import base64 as _b64
        body = _b64.b64encode(b"\x00\x00\x00\x00").decode("ascii")
        with pytest.raises(ValueError, match="unsupported mime"):
            _parse_data_url("data:image/tiff;base64," + body)

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _parse_data_url("")
        with pytest.raises(ValueError, match="non-empty"):
            _parse_data_url("   ")

    def test_non_string_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _parse_data_url(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-empty"):
            _parse_data_url(12345)  # type: ignore[arg-type]

    def test_bad_base64_rejected(self):
        with pytest.raises(ValueError, match="base64 decode failed"):
            _parse_data_url("!!!not-base64!!!")

    def test_magic_mismatch_rejected(self):
        with pytest.raises(ValueError, match="magic bytes"):
            _parse_data_url("data:image/png;base64,aGVsbG8=")

    def test_size_cap_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "src.mcp_client.host._DESCRIBE_IMAGE_MAX_BYTES", 4,
        )
        with pytest.raises(ValueError, match="too large"):
            _parse_data_url(_png_data_url())

    def test_empty_decoded_rejected(self):
        # ``data:image/png;base64,`` decodes to 0 bytes —
        # must be rejected before the magic check fires.
        with pytest.raises(ValueError, match="empty"):
            _parse_data_url("data:image/png;base64,")


# ── _write_base64_to_sandbox_tmp ────────────────────────────


class TestWriteBase64ToSandboxTmp:
    def test_writes_and_returns_path(self, tmp_path):
        target = _write_base64_to_sandbox_tmp(
            allowed_root=tmp_path,
            data_bytes=PNG_BYTES,
            mime="image/png",
        )
        assert target.exists()
        assert target.read_bytes() == PNG_BYTES
        assert target.suffix == ".png"

    def test_creates_sandbox_root(self, tmp_path):
        deep = tmp_path / "deep" / "nested"
        target = _write_base64_to_sandbox_tmp(
            allowed_root=deep,
            data_bytes=PNG_BYTES,
            mime="image/png",
        )
        assert target.exists()

    def test_stable_name_per_sha1(self, tmp_path):
        # Same bytes → same filename so concurrent calls
        # dedup naturally.
        a = _write_base64_to_sandbox_tmp(
            allowed_root=tmp_path,
            data_bytes=PNG_BYTES,
            mime="image/png",
        )
        b = _write_base64_to_sandbox_tmp(
            allowed_root=tmp_path,
            data_bytes=PNG_BYTES,
            mime="image/png",
        )
        assert a == b

    def test_ext_for_mime(self, tmp_path):
        target = _write_base64_to_sandbox_tmp(
            allowed_root=tmp_path,
            data_bytes=b"\xff\xd8\xff\xe0" + b"\x00" * 4 + b"\xff\xd9",
            mime="image/jpeg",
        )
        assert target.suffix == ".jpg"


# ── QdrantLocalHost tool listing ────────────────────────────


class TestDescribeImageListTools:
    def test_describe_image_present(self):
        host = QdrantLocalHost()
        names = {t.name for t in host.list_tools()}
        assert "describe_image" in names

    def test_describe_image_schema(self):
        host = QdrantLocalHost()
        t = next(
            x for x in host.list_tools()
            if x.name == "describe_image"
        )
        schema = t.input_schema
        props = schema["properties"]
        assert props["mode"]["enum"] == ["path", "base64"]
        for k in ("file_path", "data_base64",
                  "filename_hint", "prompt"):
            assert k in props
        assert schema["required"] == ["mode"]
        assert t.server_name == "qdrant-local"


# ── _describe_image (path mode) ─────────────────────────────


class TestDescribeImagePathMode:
    def test_success_returns_full_dict(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "shot.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(
            description="A red circle on a white background.",
            cache_state={},
        )
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "path",
                "file_path": str(img),
            })
        assert result["ok"] is True
        assert result["mode"] == "path"
        assert result["description"] == (
            "A red circle on a white background."
        )
        assert result["mime"] == "image/png"
        assert result["size_bytes"] == len(PNG_BYTES)
        assert result["sha1"] == __import__("hashlib").sha1(
            PNG_BYTES,
        ).hexdigest()
        assert result["filename"] == "shot.png"
        assert result["from_cache"] is False
        # v0.9.104.5 — the base64 blob is GONE from the result (the
        # text-only tool loop never consumed it; it cost ~58k tokens
        # per describe before being stripped again).
        assert "raw_image_base64" not in result
        assert "note" in result and "raw_image_base64" in result["note"]
        assert result["description"] == (
            "A red circle on a white background."
        )

    def test_rejects_path_outside_sandbox(
        self, tmp_path, monkeypatch,
    ):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "evil.png"
        outside.write_bytes(PNG_BYTES)
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(sandbox))
        # v0.9.16.1: the image tools accept import + filesystem
        # read/write roots — pin ALL of them so the rejection is
        # hermetic (no real WATCH_DIR leak into tmp_path).
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(sandbox))
        monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(sandbox))
        monkeypatch.setenv("WATCH_DIR", str(sandbox))

        host = _make_host_with_mocks(sandbox)
        fake = _fake_describer()
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "path",
                "file_path": str(outside),
            })
        assert "error" in result
        assert "outside the allowed sandbox roots" in result["error"]
        fake.describe.assert_not_called()

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({
            "mode": "path",
            "file_path": str(tmp_path / "ghost.png"),
        })
        assert "error" in result
        assert "not found" in result["error"]

    def test_directory_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({
            "mode": "path",
            "file_path": str(tmp_path),
        })
        assert "error" in result
        assert "not a regular file" in result["error"]

    def test_unsupported_extension(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "raw.tiff"
        img.write_bytes(b"II*\x00\x00")
        host = _make_host_with_mocks(tmp_path)

        result = host._describe_image({
            "mode": "path",
            "file_path": str(img),
        })
        assert "error" in result
        assert "unsupported image extension" in result["error"]

    def test_oversize_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.mcp_client.host._DESCRIBE_IMAGE_MAX_BYTES", 8,
        )
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "big.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 100)
        host = _make_host_with_mocks(tmp_path)

        result = host._describe_image({
            "mode": "path",
            "file_path": str(img),
        })
        assert "error" in result
        assert "too large" in result["error"]

    def test_missing_file_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({"mode": "path"})
        assert "error" in result
        assert "requires 'file_path'" in result["error"]

    def test_filename_hint_overrides_basename(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "auto_abc123.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "path",
                "file_path": str(img),
                "filename_hint": "user-friendly.png",
            })
        assert result["filename"] == "user-friendly.png"

    def test_dotdot_traversal_blocked(
        self, tmp_path, monkeypatch,
    ):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        escape = sandbox / ".." / "evil.png"
        escape.write_bytes(PNG_BYTES)
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(sandbox))
        # v0.9.16.1: pin every accepted root hermetically.
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(sandbox))
        monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(sandbox))
        monkeypatch.setenv("WATCH_DIR", str(sandbox))

        host = _make_host_with_mocks(sandbox)
        result = host._describe_image({
            "mode": "path",
            "file_path": str(escape),
        })
        assert "error" in result
        assert "outside the allowed sandbox roots" in result["error"]


# ── _describe_image (base64 mode) ───────────────────────────


class TestDescribeImageBase64Mode:
    def test_success_bare_base64(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "base64",
                "data_base64": _png_b64(),
            })
        assert result["ok"] is True
        assert result["mode"] == "base64"
        assert result["mime"] == "image/png"
        assert result["size_bytes"] == len(PNG_BYTES)
        assert result["description"].startswith("A test image")
        # Auto-generated filename includes sha1 prefix.
        assert result["filename"].startswith("inline_")
        assert result["filename"].endswith(".png")

    def test_success_data_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "base64",
                "data_base64": _png_data_url(),
            })
        assert result["ok"] is True
        assert result["mime"] == "image/png"

    def test_filename_hint_used_for_tmp(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "base64",
                "data_base64": _png_b64(),
                "filename_hint": "pasted.png",
            })
        assert result["filename"] == "pasted.png"

    def test_filename_hint_strips_path_separators(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "base64",
                "data_base64": _png_b64(),
                "filename_hint": "../escape/evil.png",
            })
        # ``os.path.basename`` keeps only the leaf.
        assert "/" not in result["filename"]
        assert "\\" not in result["filename"]
        assert result["filename"] == "evil.png"

    def test_tmp_file_cleaned_up_after_success(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            host._describe_image({
                "mode": "base64",
                "data_base64": _png_b64(),
            })
        leftovers = list(tmp_path.glob(".describe_*"))
        assert leftovers == []

    def test_tmp_file_cleaned_up_after_failure(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(
            side_effect=RuntimeError("network down"),
        )
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "base64",
                "data_base64": _png_b64(),
            })
        assert "error" in result
        assert "vision call failed" in result["error"]
        leftovers = list(tmp_path.glob(".describe_*"))
        assert leftovers == []

    def test_invalid_base64_payload(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({
            "mode": "base64",
            "data_base64": "not-base64-at-all!!!",
        })
        assert "error" in result
        assert "base64 decode failed" in result["error"]

    def test_magic_mismatch_in_payload(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({
            "mode": "base64",
            "data_base64": "data:image/png;base64,aGVsbG8=",
        })
        assert "error" in result
        assert "magic bytes" in result["error"]

    def test_missing_data_base64(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({"mode": "base64"})
        assert "error" in result


# ── _describe_image (mode validation) ──────────────────────


class TestDescribeImageModeValidation:
    def test_missing_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({})
        assert "error" in result
        assert "'mode' must be" in result["error"]

    def test_invalid_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image({"mode": "magic"})
        assert "error" in result
        assert "'mode' must be" in result["error"]

    def test_non_dict_payload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._describe_image(
            "not a dict",  # type: ignore[arg-type]
        )
        assert "error" in result
        assert "expects a dict" in result["error"]


# ── v0.9.104.5 — shared SHA-1 cache + no base64 blob ─────────


class TestSharedCacheV0104_5:
    def test_cache_shared_across_instances(self, tmp_path, monkeypatch):
        """Two FRESH ImageDescriber instances (the MCP host builds one
        per call) must share the SHA-1 cache — the second describe is
        a hit with zero extra vision API calls."""
        from unittest.mock import patch

        from src.ingest.image_describer import (
            ImageDescriber,
            _shared_cache_clear,
        )

        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "x.png"
        img.write_bytes(PNG_BYTES)
        _shared_cache_clear()
        calls = []

        def _fake_call(self, data_url, *a, **k):
            calls.append(1)
            return "shared desc"

        try:
            with patch.object(ImageDescriber, "_call_openai_compat",
                              _fake_call):
                d1 = ImageDescriber()
                out1 = d1.describe(img)
                d2 = ImageDescriber()  # fresh instance, empty _cache
                out2 = d2.describe(img)
            assert out1 == out2 == "shared desc"
            assert len(calls) == 1          # 2nd = shared-cache hit
            assert d1.last_cache_hit is False
            assert d2.last_cache_hit is True
        finally:
            _shared_cache_clear()

    def test_host_result_slim_and_cache_aware(self, tmp_path, monkeypatch):
        """Host-level E2E: no base64 blob in the result; the SECOND
        identical describe carries from_cache=true without another
        vision call."""
        import json as _json
        from unittest.mock import patch

        from src.ingest.image_describer import (
            ImageDescriber,
            _shared_cache_clear,
        )

        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "x.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host_with_mocks(tmp_path)
        _shared_cache_clear()
        calls = []

        def _fake_call(self, data_url, *a, **k):
            calls.append(1)
            return "desc only"

        try:
            with patch.object(ImageDescriber, "_call_openai_compat",
                              _fake_call):
                r1 = host._describe_image({
                    "mode": "path", "file_path": str(img)})
                r2 = host._describe_image({
                    "mode": "path", "file_path": str(img)})
            assert r1["ok"] is True
            assert "raw_image_base64" not in r1
            assert "raw_image_base64" in r1["note"]
            assert len(_json.dumps(r1, ensure_ascii=False)) < 4000
            assert r1["from_cache"] is False
            assert r2["from_cache"] is True   # shared hit, no 2nd call
            assert r2["description"] == "desc only"
            assert len(calls) == 1
        finally:
            _shared_cache_clear()


class TestDescribeImageCustomPrompt:
    def test_custom_prompt_used_and_restored(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "x.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            host._describe_image({
                "mode": "path",
                "file_path": str(img),
                "prompt": "Just count objects please.",
            })
        assert fake.describe.called
        # Inspect the describer.PROMPT AFTER the call to make
        # sure the override was restored.
        assert fake.PROMPT == "default bilingual prompt"

    def test_cache_hit_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "x.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host_with_mocks(tmp_path)

        import hashlib as _hl
        sha = _hl.sha1(PNG_BYTES).hexdigest()
        cached_desc = "cached description"
        cache_state = {sha: cached_desc}
        fake = _fake_describer(
            description=cached_desc, cache_state=cache_state,
        )
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "path",
                "file_path": str(img),
            })
        assert result["from_cache"] is True
        assert result["description"] == cached_desc

    def test_vision_call_exception_returns_dict(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "x.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host_with_mocks(tmp_path)

        fake = _fake_describer(
            side_effect=RuntimeError("ollama 5xx"),
        )
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "path",
                "file_path": str(img),
            })
        assert "error" in result
        assert "ollama 5xx" in result["error"]


# ── LocalTool wrapper integration ──────────────────────────


class TestLocalToolWrapperIntegration:
    """End-to-end through ``LocalTool.__call__`` so we exercise
    the same path the Agent's tool-calling loop uses."""

    def test_describe_image_wrapper_dict_args(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        img = tmp_path / "x.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host_with_mocks(tmp_path)
        tool = next(
            t for t in host.list_tools()
            if t.name == "describe_image"
        )

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            result = tool({
                "args": {
                    "mode": "path",
                    "file_path": str(img),
                },
            })
        assert result.is_error is False
        assert '"ok": true' in result.text

    def test_describe_image_kw_spread_args(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        tool = next(
            t for t in host.list_tools()
            if t.name == "describe_image"
        )

        fake = _fake_describer(cache_state={})
        with patch(
            _PATCH_IMG_DESC,
            return_value=fake,
        ):
            # No wrapper — bare top-level kwargs (the path most
            # OpenAI-compatible providers use).
            result = tool({
                "mode": "base64",
                "data_base64": _png_b64(),
            })
        assert result.is_error is False
        assert "A test image" in result.text


# ── sanity ──────────────────────────────────────────────────


def test_describe_image_mimes_include_canonical():
    for mime in ("image/png", "image/jpeg", "image/gif",
                 "image/webp", "image/bmp", "image/svg+xml"):
        assert mime in _DESCRIBE_IMAGE_MIMES


def test_image_magic_table_covers_mimes():
    """Every raster mime in ``_DESCRIBE_IMAGE_MIMES`` has an
    entry in ``_IMAGE_MAGIC`` (SVG is the deliberate
    exception)."""
    raster = {"image/png", "image/jpeg", "image/gif",
              "image/webp", "image/bmp"}
    assert raster <= set(_IMAGE_MAGIC.keys())
    # SVG is XML, no magic — confirmed absent.
    assert "image/svg+xml" not in _IMAGE_MAGIC