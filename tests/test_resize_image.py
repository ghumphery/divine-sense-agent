"""Tests for v0.9.2 QdrantLocalHost.resize_image.

Covers the new LLM-driven image downscale tool exposed on the
``qdrant-local`` server. Use case: ``describe_image`` returns
``too large``; the LLM calls ``resize_image`` to shrink the
file under the 4 MB vision cap, then re-calls ``describe_image``
on the output.

All Pillow work runs against real files in tmp_path; no
network / Qdrant / Ollama mocks needed for the core paths.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Pillow is a hard dep declared in v0.9.2+; skip the whole
# module on a 3rd-party install where the package isn't there
# yet.
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from src.mcp_client.host import (
    QdrantLocalHost,
    _DESCRIBE_IMAGE_MAX_BYTES,
    _RESIZE_DEFAULT_FORMAT,
    _RESIZE_DEFAULT_QUALITY,
)


# ── helpers ────────────────────────────────────────────────


def _make_big_png(tmp_path: Path, name: str, w: int, h: int) -> Path:
    """Build a real on-disk PNG with the requested size."""
    img = Image.new("RGB", (w, h), color=(128, 128, 128))
    # Add some variance so resizing isn't a no-op on flat colour.
    pixels = img.load()
    for x in range(0, w, 50):
        for y in range(0, h, 50):
            pixels[x, y] = ((x * 17) % 256, (y * 31) % 256, 128)
    p = tmp_path / name
    img.save(p, format="PNG")
    return p


def _make_host_with_mocks(tmp_path):
    """Build a QdrantLocalHost with the QdrantStore / Retriever
    stubbed out so ``__init__`` doesn't reach for Qdrant."""
    host = QdrantLocalHost()
    host._store = MagicMock()
    host._store.list_documents.return_value = []
    host._retriever = MagicMock()
    return host


# ── sanity: defaults + module-level constants ──────────────


class TestResizeImageConstants:
    def test_defaults(self):
        assert _RESIZE_DEFAULT_FORMAT == "jpeg"
        assert _RESIZE_DEFAULT_QUALITY == 85
        assert _DESCRIBE_IMAGE_MAX_BYTES == 4 * 1024 * 1024


# ── QdrantLocalHost tool listing ────────────────────────────


class TestResizeImageListTools:
    def test_resize_image_present(self):
        host = QdrantLocalHost()
        names = {t.name for t in host.list_tools()}
        assert "resize_image" in names

    def test_resize_image_schema(self):
        host = QdrantLocalHost()
        t = next(
            x for x in host.list_tools()
            if x.name == "resize_image"
        )
        assert t.server_name == "qdrant-local"
        schema = t.input_schema
        assert schema["required"] == ["file_path", "max_width"]
        props = schema["properties"]
        for k in (
            "file_path", "max_width", "max_height",
            "out_filename", "format", "quality",
        ):
            assert k in props
        # format is a closed enum
        assert props["format"]["enum"] == ["jpeg", "png", "webp"]
        # quality has sane bounds
        assert props["quality"]["minimum"] == 1
        assert props["quality"]["maximum"] == 100


# ── happy-path: real Pillow downscale ────────────────────────


class TestResizeImageHappyPath:
    def test_downscale_preserves_aspect_ratio(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "huge.png", 4000, 2000)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 800,
        })
        assert result["ok"] is True
        assert result["original_dimensions"] == [4000, 2000]
        # 4000/2000 = 2:1, so 800x400. Verify Pillow actually
        # downscaled.
        assert result["output_dimensions"] == [800, 400]
        out_path = Path(result["output_path"])
        assert out_path.exists()
        with Image.open(out_path) as im:
            assert im.size == (800, 400)

    def test_default_output_jpeg(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "big.png", 2000, 2000)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 500,
        })
        assert result["format"] == "jpeg"
        # The default out_filename is ``<stem>_resized.<fmt>``.
        assert result["output_path"].endswith("big_resized.jpeg")

    def test_explicit_out_filename(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "x.png", 1000, 1000)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 200,
            "out_filename": "small.png",
        })
        assert result["output_path"].endswith("small.png")
        assert Path(result["output_path"]).exists()

    def test_jpeg_smaller_than_png(
        self, tmp_path, monkeypatch,
    ):
        """Noisy 1000x1000 JPEG at q=85 must be smaller than
        the equivalent PNG (lossless)."""
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "src.png", 1000, 1000)
        host = _make_host_with_mocks(tmp_path)

        jpeg_out = host._resize_image({
            "file_path": str(src),
            "max_width": 800,
            "format": "jpeg",
            "quality": 85,
        })
        png_out = host._resize_image({
            "file_path": str(src),
            "max_width": 800,
            "format": "png",
            "out_filename": "noise.png",
        })
        assert jpeg_out["ok"] is True
        assert png_out["ok"] is True
        # JPEG is lossy, PNG is lossless. For noisy content
        # JPEG must be smaller — that's the whole point of
        # defaulting to JPEG.
        assert jpeg_out["output_size_bytes"] < (
            png_out["output_size_bytes"]
        )

    def test_within_cap_signal(
        self, tmp_path, monkeypatch,
    ):
        """A 4000x4000 noisy PNG downscaled to 1024 max should
        produce a file comfortably under the 4 MB cap."""
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "huge.png", 4000, 4000)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 1024,
        })
        assert result["within_cap"] is True
        assert result["output_size_bytes"] <= _DESCRIBE_IMAGE_MAX_BYTES

    def test_already_small_no_op(
        self, tmp_path, monkeypatch,
    ):
        """Image already fits — return as-is (still transcode)."""
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "small.png", 200, 100)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 800,
        })
        assert result["original_dimensions"] == [200, 100]
        assert result["output_dimensions"] == [200, 100]

    def test_max_height_independent(
        self, tmp_path, monkeypatch,
    ):
        """max_height caps the tall axis independently."""
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        # 4000 wide, 1000 tall (4:1 aspect).
        src = _make_big_png(tmp_path, "wide.png", 4000, 1000)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 9999,  # effectively unlimited
            "max_height": 500,
        })
        assert result["output_dimensions"] == [2000, 500]

    def test_raw_image_base64_round_trip(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "x.png", 500, 500)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 200,
        })
        # The base64 in the response is the actual file.
        out_bytes = base64.b64decode(result["raw_image_base64"])
        with Image.open(io.BytesIO(out_bytes)) as im:
            assert im.size == (200, 200)


# ── EXIF preservation ───────────────────────────────────────


class TestResizeImageExif:
    def test_jpeg_with_exif_preserves_segment(
        self, tmp_path, monkeypatch,
    ):
        """Write a JPEG with an EXIF segment, resize, verify
        the output also has an EXIF segment of non-zero length.
        """
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = tmp_path / "with_exif.jpg"
        img = Image.new("RGB", (500, 500), color=(64, 128, 192))
        # Build a real (minimal) EXIF block via Pillow so it
        # passes the TiffImagePlugin parse. Just an
        # ``Orientation = 1`` tag in IFD0.
        from PIL.ExifTags import Base as ExifBase
        from PIL.Image import Exif as PillowExif
        exif = PillowExif()
        # ``Orientation`` is tag 0x0112, type 3 (SHORT), count 1
        exif.setdefault(0x0112, 1)
        exif_bytes = exif.tobytes()
        img.save(src, format="JPEG", exif=exif_bytes)

        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({
            "file_path": str(src),
            "max_width": 200,
        })
        assert result["ok"] is True
        with Image.open(result["output_path"]) as im:
            exif_after = im.info.get("exif", b"")
        assert exif_after != b""
        assert len(exif_after) >= len(exif_bytes)


# ── sandbox + argument validation ────────────────────────────


class TestResizeImageSandbox:
    def test_path_outside_sandbox_rejected(
        self, tmp_path, monkeypatch,
    ):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "evil.png"
        _make_big_png(tmp_path, "evil.png", 200, 200)
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(sandbox))
        # v0.9.16.1: pin every accepted root hermetically.
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(sandbox))
        monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(sandbox))
        monkeypatch.setenv("WATCH_DIR", str(sandbox))

        host = _make_host_with_mocks(sandbox)
        result = host._resize_image({
            "file_path": str(outside),
            "max_width": 100,
        })
        assert "error" in result
        assert "outside the allowed sandbox roots" in result["error"]

    def test_missing_file_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({
            "file_path": str(tmp_path / "ghost.png"),
            "max_width": 100,
        })
        assert "error" in result
        assert "not found" in result["error"]

    def test_directory_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({
            "file_path": str(tmp_path),
            "max_width": 100,
        })
        assert "error" in result
        assert "not a regular file" in result["error"]

    def test_dotdot_escape_blocked(
        self, tmp_path, monkeypatch,
    ):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        escape = sandbox / ".." / "evil.png"
        _make_big_png(tmp_path, "evil.png", 200, 200)
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(sandbox))
        # v0.9.16.1: pin every accepted root hermetically.
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(sandbox))
        monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(sandbox))
        monkeypatch.setenv("WATCH_DIR", str(sandbox))

        host = _make_host_with_mocks(sandbox)
        result = host._resize_image({
            "file_path": str(escape),
            "max_width": 100,
        })
        assert "error" in result
        assert "outside the allowed sandbox roots" in result["error"]

    def test_output_filename_strips_path_separators(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "x.png", 200, 200)
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 100,
            "out_filename": "../escape.png",
        })
        # ``os.path.basename`` keeps the leaf — lands inside
        # the sandbox.
        assert result["ok"] is True
        assert "escape.png" in result["output_path"]

    def test_existing_output_rejected(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "src.png", 200, 200)
        # Pre-existing file with the default output name.
        (tmp_path / "src_resized.jpeg").write_bytes(b"existing")
        host = _make_host_with_mocks(tmp_path)

        result = host._resize_image({
            "file_path": str(src),
            "max_width": 100,
        })
        assert "error" in result
        assert "already exists" in result["error"]


# ── argument validation ─────────────────────────────────────


class TestResizeImageArgumentValidation:
    def test_missing_file_path(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({"max_width": 100})
        assert "error" in result
        assert "'file_path' is required" in result["error"]

    def test_missing_max_width(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({"file_path": "x.png"})
        assert "error" in result
        assert "'max_width' must be" in result["error"]

    def test_zero_max_width_rejected(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({
            "file_path": "x.png", "max_width": 0,
        })
        assert "error" in result
        assert "positive integer" in result["error"]

    def test_string_max_width_rejected(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({
            "file_path": "x.png", "max_width": "800",
        })
        assert "error" in result

    def test_invalid_format_rejected(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image({
            "file_path": "x.png", "max_width": 100,
            "format": "tiff",
        })
        assert "error" in result
        assert "format" in result["error"]

    def test_quality_out_of_range(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        for bad in (-1, 0, 101, 200):
            result = host._resize_image({
                "file_path": "x.png", "max_width": 100,
                "quality": bad,
            })
            assert "error" in result
            assert "quality" in result["error"]

    def test_non_dict_payload(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        host = _make_host_with_mocks(tmp_path)
        result = host._resize_image(
            "not a dict",  # type: ignore[arg-type]
        )
        assert "error" in result
        assert "expects a dict" in result["error"]


# ── atomic-write: failure leaves no half-written target ──────


class TestResizeImageAtomicWrite:
    def test_pillow_failure_no_leftover_target(
        self, tmp_path, monkeypatch,
    ):
        """If ``Image.save`` raises, the destination must
        NOT exist — atomic write guarantees this."""
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "x.png", 500, 500)
        host = _make_host_with_mocks(tmp_path)

        def boom_save(self, fp, format=None, **kwargs):
            raise RuntimeError("disk full mid-save")

        with patch.object(Image.Image, "save", boom_save):
            result = host._resize_image({
                "file_path": str(src),
                "max_width": 200,
            })
        assert "error" in result
        assert "disk full" in result["error"]
        # The default output (``x_resized.jpeg``) must NOT
        # exist on disk — atomic write rolled back.
        assert not (tmp_path / "x_resized.jpeg").exists()


# ── LocalTool wrapper integration ──────────────────────────


class TestLocalToolWrapperIntegration:
    def test_wrapper_dict_args(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "x.png", 500, 500)
        host = _make_host_with_mocks(tmp_path)
        tool = next(
            t for t in host.list_tools()
            if t.name == "resize_image"
        )
        result = tool({
            "args": {
                "file_path": str(src),
                "max_width": 200,
            },
        })
        assert result.is_error is False
        assert '"ok": true' in result.text

    def test_kw_spread_args(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        src = _make_big_png(tmp_path, "x.png", 500, 500)
        host = _make_host_with_mocks(tmp_path)
        tool = next(
            t for t in host.list_tools()
            if t.name == "resize_image"
        )
        # Bare top-level kwargs.
        result = tool({
            "file_path": str(src),
            "max_width": 200,
        })
        assert result.is_error is False