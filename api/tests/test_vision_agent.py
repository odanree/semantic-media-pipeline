"""
Tests for vision_agent.py using a Base64-encoded minimal in-memory JPEG
fixture — no GPU, no live camera, no filesystem I/O to real media files.

Strategy
--------
- A 1×1 white JPEG is constructed in-memory at module level and written to a
  tmp_path fixture so _analyze_frame() sees a real file that can be opened and
  base64-encoded without hitting any ML stack.
- The LLM `complete()` call is mocked via AsyncMock so no network request is made.
- We test both the high-level vision_agent_run() entry-point and the lower-level
  _analyze_frame() helper to maximise line coverage.
"""

import asyncio
import base64
import io
import struct
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal JPEG/PNG builders — no Pillow required
# ---------------------------------------------------------------------------

def _make_1x1_jpeg() -> bytes:
    """
    Construct a valid 1×1 white JPEG entirely from spec bytes.
    This is the smallest legal JPEG that most decoders accept.
    """
    return bytes([
        # SOI
        0xFF, 0xD8,
        # APP0 JFIF marker
        0xFF, 0xE0, 0x00, 0x10,
        0x4A, 0x46, 0x49, 0x46, 0x00,  # "JFIF\0"
        0x01, 0x01,                      # version 1.1
        0x00,                            # pixel aspect ratio units = 0
        0x00, 0x01, 0x00, 0x01,          # X/Y density = 1
        0x00, 0x00,                      # no thumbnail
        # DQT — quantization table (all-ones = lossless quality)
        0xFF, 0xDB, 0x00, 0x43, 0x00,
        *([0x01] * 64),
        # SOF0 — baseline DCT, 1×1 pixel, grayscale
        0xFF, 0xC0, 0x00, 0x0B, 0x08,
        0x00, 0x01, 0x00, 0x01,          # height=1, width=1
        0x01,                            # 1 component (grayscale)
        0x01, 0x11, 0x00,
        # DHT — Huffman table (DC)
        0xFF, 0xC4, 0x00, 0x1F, 0x00,
        0x00, 0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01,
        0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0A, 0x0B,
        # SOS — start of scan
        0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00,
        0x3F, 0x00,
        0xF8,  # single DCT coefficient
        # EOI
        0xFF, 0xD9,
    ])


def _make_1x1_png() -> bytes:
    """Build a minimal valid 1×1 red PNG from scratch."""
    def _chunk(name: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    # 1×1 RGB: filter 0x00, then R=255 G=0 B=0
    raw = zlib.compress(b"\x00\xFF\x00\x00")
    idat = _chunk(b"IDAT", raw)
    iend = _chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


# Precomputed base64 strings — used in message-content assertions
_JPEG_B64 = base64.b64encode(_make_1x1_jpeg()).decode()
_PNG_B64 = base64.b64encode(_make_1x1_png()).decode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def jpeg_file(tmp_path) -> Path:
    p = tmp_path / "frame.jpg"
    p.write_bytes(_make_1x1_jpeg())
    return p


@pytest.fixture()
def jpeg_file_no_ext(tmp_path) -> Path:
    """Simulates a file whose extension doesn't map to a known image MIME type."""
    p = tmp_path / "frame.bin"
    p.write_bytes(_make_1x1_jpeg())
    return p


@pytest.fixture()
def png_file(tmp_path) -> Path:
    p = tmp_path / "frame.png"
    p.write_bytes(_make_1x1_png())
    return p


@pytest.fixture()
def webp_file(tmp_path) -> Path:
    # Minimal WebP (RIFF header only — enough for our path/MIME logic test)
    p = tmp_path / "thumb.webp"
    p.write_bytes(b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20)
    return p


# ---------------------------------------------------------------------------
# _analyze_frame() unit tests
# ---------------------------------------------------------------------------

class TestAnalyzeFrame:
    def test_returns_none_for_nonexistent_file(self):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        result = asyncio.run(_analyze_frame(llm, "/no/such/path/image.jpg"))
        assert result is None
        llm.complete.assert_not_called()

    def test_happy_path_jpeg_returns_description(self, jpeg_file):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="A bright white frame.")
        result = asyncio.run(_analyze_frame(llm, str(jpeg_file)))
        assert result == "A bright white frame."

    def test_jpeg_uses_image_jpg_mime_type(self, jpeg_file):
        from agents.vision_agent import _analyze_frame

        # The implementation uses the raw suffix as the MIME subtype:
        # .jpg → image/jpg, .jpeg → image/jpeg
        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(jpeg_file)))
        messages = llm.complete.call_args.kwargs["messages"]
        image_url = messages[1]["content"][0]["image_url"]["url"]
        assert image_url.startswith("data:image/jpg;base64,")

    def test_jpeg_file_content_is_correctly_base64_encoded(self, jpeg_file):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(jpeg_file)))
        messages = llm.complete.call_args.kwargs["messages"]
        url = messages[1]["content"][0]["image_url"]["url"]
        # Strip the data URI prefix and verify the bytes round-trip
        _, b64_part = url.split(",", 1)
        decoded = base64.b64decode(b64_part)
        assert decoded == jpeg_file.read_bytes()

    def test_png_uses_image_png_mime_type(self, png_file):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(png_file)))
        messages = llm.complete.call_args.kwargs["messages"]
        url = messages[1]["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_webp_uses_image_webp_mime_type(self, webp_file):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(webp_file)))
        messages = llm.complete.call_args.kwargs["messages"]
        url = messages[1]["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/webp;base64,")

    def test_unknown_extension_falls_back_to_image_jpeg(self, jpeg_file_no_ext):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(jpeg_file_no_ext)))
        messages = llm.complete.call_args.kwargs["messages"]
        url = messages[1]["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_system_prompt_included_in_messages(self, jpeg_file):
        from agents.vision_agent import _analyze_frame, _VISION_SYSTEM

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(jpeg_file)))
        messages = llm.complete.call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == _VISION_SYSTEM

    def test_user_message_has_text_and_image_parts(self, jpeg_file):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(jpeg_file)))
        messages = llm.complete.call_args.kwargs["messages"]
        user_content = messages[1]["content"]
        types = {part["type"] for part in user_content}
        assert "image_url" in types
        assert "text" in types

    def test_max_tokens_passed_as_256(self, jpeg_file):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="desc")
        asyncio.run(_analyze_frame(llm, str(jpeg_file)))
        assert llm.complete.call_args.kwargs["max_tokens"] == 256

    def test_llm_exception_returns_none_and_does_not_propagate(self, jpeg_file):
        from agents.vision_agent import _analyze_frame

        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("vision model unavailable"))
        result = asyncio.run(_analyze_frame(llm, str(jpeg_file)))
        assert result is None


# ---------------------------------------------------------------------------
# vision_agent_run() integration tests
# ---------------------------------------------------------------------------

class TestVisionAgentRun:
    def test_empty_search_results_returns_empty_list(self):
        from agents.vision_agent import vision_agent_run

        result = asyncio.run(vision_agent_run([]))
        assert result == []

    def test_non_image_file_types_filtered_out(self, tmp_path):
        from agents.vision_agent import vision_agent_run

        results = [
            {"file_path": "clip.mp4", "file_type": "video"},
            {"file_path": "song.mp3", "file_type": "audio"},
        ]
        with patch("agents.vision_agent.get_llm_provider"):
            result = asyncio.run(vision_agent_run(results))
        assert result == []

    def test_only_images_are_passed_to_analyze(self, jpeg_file, tmp_path):
        from agents.vision_agent import vision_agent_run

        # Mix of image and video — only the image should be analyzed
        video_path = str(tmp_path / "clip.mp4")
        results = [
            {"file_path": str(jpeg_file), "file_type": "image"},
            {"file_path": video_path, "file_type": "video"},
        ]
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="A white frame.")
        with patch("agents.vision_agent.get_llm_provider", return_value=mock_llm):
            output = asyncio.run(vision_agent_run(results))
        assert len(output) == 1
        assert output[0]["file_path"] == str(jpeg_file)
        assert output[0]["description"] == "A white frame."

    def test_max_frames_limit_respected(self, tmp_path):
        from agents.vision_agent import vision_agent_run

        # Create 5 real image files but MAX_FRAMES_TO_ANALYZE defaults to 3
        files = []
        for i in range(5):
            p = tmp_path / f"frame{i}.jpg"
            p.write_bytes(_make_1x1_jpeg())
            files.append({"file_path": str(p), "file_type": "image"})

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="description")
        with patch("agents.vision_agent.get_llm_provider", return_value=mock_llm):
            with patch("agents.vision_agent.MAX_FRAMES_TO_ANALYZE", 3):
                output = asyncio.run(vision_agent_run(files))
        assert len(output) == 3

    def test_missing_files_are_omitted_from_output(self, tmp_path):
        from agents.vision_agent import vision_agent_run

        real_file = tmp_path / "real.jpg"
        real_file.write_bytes(_make_1x1_jpeg())
        results = [
            {"file_path": str(real_file), "file_type": "image"},
            {"file_path": "/nonexistent/ghost.jpg", "file_type": "image"},
        ]
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="real description")
        with patch("agents.vision_agent.get_llm_provider", return_value=mock_llm):
            output = asyncio.run(vision_agent_run(results))
        assert len(output) == 1
        assert output[0]["file_path"] == str(real_file)

    def test_llm_failure_for_one_frame_does_not_stop_others(self, tmp_path):
        from agents.vision_agent import vision_agent_run

        f1 = tmp_path / "ok.jpg"
        f1.write_bytes(_make_1x1_jpeg())
        f2 = tmp_path / "fail.jpg"
        f2.write_bytes(_make_1x1_jpeg())
        f3 = tmp_path / "ok2.jpg"
        f3.write_bytes(_make_1x1_jpeg())

        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("transient error")
            return f"description {call_count}"

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(side_effect=_side_effect)
        results = [
            {"file_path": str(f1), "file_type": "image"},
            {"file_path": str(f2), "file_type": "image"},
            {"file_path": str(f3), "file_type": "image"},
        ]
        with patch("agents.vision_agent.get_llm_provider", return_value=mock_llm):
            with patch("agents.vision_agent.MAX_FRAMES_TO_ANALYZE", 3):
                output = asyncio.run(vision_agent_run(results))
        # f2 fails silently — 2 of 3 succeed
        assert len(output) == 2
        assert output[0]["file_path"] == str(f1)
        assert output[1]["file_path"] == str(f3)

    def test_output_contains_file_path_and_description_keys(self, jpeg_file):
        from agents.vision_agent import vision_agent_run

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="A detailed scene.")
        with patch("agents.vision_agent.get_llm_provider", return_value=mock_llm):
            output = asyncio.run(vision_agent_run(
                [{"file_path": str(jpeg_file), "file_type": "image"}]
            ))
        assert "file_path" in output[0]
        assert "description" in output[0]

    def test_jpeg_extension_variants_recognized(self, tmp_path):
        from agents.vision_agent import _analyze_frame

        for ext in ("jpg", "jpeg"):
            p = tmp_path / f"img.{ext}"
            p.write_bytes(_make_1x1_jpeg())
            llm = MagicMock()
            llm.complete = AsyncMock(return_value="ok")
            asyncio.run(_analyze_frame(llm, str(p)))
            url = llm.complete.call_args.kwargs["messages"][1]["content"][0]["image_url"]["url"]
            assert f"data:image/{ext};base64," in url
