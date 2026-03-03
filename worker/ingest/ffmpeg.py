"""
FFmpeg Integration - Video processing and frame extraction
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2


class FFmpegError(Exception):
    """FFmpeg processing error"""

    pass


def probe_media(file_path: str) -> Dict:
    """
    Use ffprobe to get media metadata.

    Returns:
        Dictionary with keys: duration, width, height, codec_name, frame_rate
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise FFmpegError(f"ffprobe failed: {result.stderr}")

        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        format_data = data.get("format", {})

        # Find first video stream
        video_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None
        )

        if not video_stream:
            raise FFmpegError("No video stream found")

        metadata = {
            "duration": float(format_data.get("duration", 0)),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "codec_name": video_stream.get("codec_name", "unknown"),
            "frame_rate": video_stream.get("r_frame_rate", "30/1"),
        }

        return metadata
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"ffprobe timeout on {file_path}")
    except json.JSONDecodeError:
        raise FFmpegError(f"ffprobe output parsing failed for {file_path}")


def extract_keyframes(
    video_path: str,
    output_dir: str,
    fps: float = 0.5,
    resolution: int = 224,
    timeout: Optional[int] = None,
    video_duration: Optional[float] = None,
) -> List[str]:
    """
    Extract keyframes from a video at specified FPS and resolution.
    Uses FFmpeg to extract frames and scales to resolution×resolution.

    Args:
        video_path: Path to input video
        output_dir: Directory to save extracted JPEG frames
        fps: Frames per second to extract (default 0.5 = 1 frame per 2 seconds)
        resolution: Target square resolution for CLIP (default 224x224)
        timeout: Timeout in seconds (optional, auto-calculated if not provided)
        video_duration: Video duration in seconds (optional, for timeout scaling)

    Returns:
        List of paths to extracted frames
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Calculate timeout if not provided
    if timeout is None:
        # Try to get video duration for smart timeout scaling
        if video_duration is None:
            try:
                metadata = probe_media(video_path)
                video_duration = metadata.get("duration", 0)
            except FFmpegError:
                video_duration = 0
        
        # Calculate timeout: 120s base + 2s per second of video content
        # This accounts for I/O overhead and processing time
        base_timeout = int(os.getenv("FFMPEG_TIMEOUT", "1200"))  # 20 minutes default
        if video_duration > 0:
            # For longer videos, scale up the timeout proportionally
            # 2x video duration as safety margin for slow I/O
            computed_timeout = int(base_timeout + (video_duration * 1.5))
            timeout = max(base_timeout, computed_timeout)
            print(f"[FFmpeg] Video: {video_path}")
            print(f"[FFmpeg] Duration: {video_duration:.1f}s | Base timeout: {base_timeout}s | Computed: {computed_timeout}s | Final: {timeout}s")
        else:
            timeout = base_timeout
            print(f"[FFmpeg] Video: {video_path}")
            print(f"[FFmpeg] Duration: unknown | Base timeout (default): {timeout}s")

    frame_pattern = os.path.join(output_dir, "frame_%04d.jpg")

    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-vf",
        f"fps={fps},scale={resolution}:{resolution}:force_original_aspect_ratio=decrease,pad={resolution}:{resolution}:(ow-iw)/2:(oh-ih)/2:color=black",
        "-q:v",
        "2",  # JPEG quality
        frame_pattern,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0 and "No such file or directory" not in result.stderr:
            # Check if at least some frames were extracted
            frame_files = sorted(Path(output_dir).glob("frame_*.jpg"))
            if not frame_files:
                raise FFmpegError(f"FFmpeg failed: {result.stderr}")

        # Return extracted frames
        frame_files = sorted(Path(output_dir).glob("frame_*.jpg"))
        return [str(f) for f in frame_files]

    except subprocess.TimeoutExpired:
        msg = f"[FFmpeg] TIMEOUT: {timeout}s limit exceeded"
        if video_duration and video_duration > 0:
            msg += f" (video: {video_duration:.1f}s)"
        msg += f"\nSet FFMPEG_TIMEOUT={timeout * 2} in .env to allow more time"
        raise FFmpegError(msg)
    except Exception as e:
        raise FFmpegError(f"Frame extraction failed: {str(e)}")


def extract_thumbnail(
    video_path: str, output_path: str, timestamp: float = 5.0, resolution: int = 224
) -> str:
    """
    Extract a single thumbnail from a video.

    Args:
        video_path: Path to input video
        output_path: Path to save thumbnail
        timestamp: Timestamp in seconds (default 5 minutes in)
        resolution: Target square resolution (default 224x224)

    Returns:
        Path to extracted thumbnail
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-ss",
        str(timestamp),
        "-vframes",
        "1",
        "-vf",
        f"scale={resolution}:{resolution}:force_original_aspect_ratio=decrease,pad={resolution}:{resolution}:(ow-iw)/2:(oh-ih)/2:color=black",
        "-q:v",
        "2",
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise FFmpegError(f"Thumbnail extraction failed: {result.stderr}")

        return output_path

    except subprocess.TimeoutExpired:
        raise FFmpegError(f"FFmpeg timeout extracting thumbnail from {video_path}")


def apply_faststart(
    video_path: str,
    output_path: Optional[str] = None,
    video_duration: Optional[float] = None,
) -> bool:
    """
    Produce a browser-ready version of an MP4/MOV file.

    Two modes depending on whether output_path is supplied:

    Proxy mode  (output_path provided — Zero-Mutation Architecture)
        Transcodes to a crushed 720p H.264/AAC copy with moov-first.
        • scale=-2:720   preserves aspect ratio for both landscape AND portrait
        • CRF 28 + veryfast preset  ≈ 1:20 size ratio vs. 4K original
        • ~25 GB proxies for a 500 GB library vs 500 GB for a -c copy
        • Timeout scales with video_duration (pass metadata["duration"] from
          tasks.py); falls back to a 30 min ceiling if unknown.
        File is written to output_path on the :rw proxies_data volume.
        Source is never touched.

    In-place mode (output_path is None)
        Stream-copies (no re-encode) with -movflags +faststart and atomically
        replaces the source.  Requires a writable mount — logs a clear message
        and returns False on PermissionError (:ro source).

    Returns True if a file was written, False if already optimised, wrong
    container, proxy already exists, or :ro PermissionError.
    """
    path = Path(video_path)
    suffix = path.suffix.lower()
    if suffix not in (".mp4", ".m4v", ".mov"):
        return False

    # Proxy mode: idempotent — skip if proxy already exists
    if output_path and Path(output_path).is_file():
        print(f"[Faststart] Proxy already exists, skipping: {output_path}")
        return False

    # In-place mode: skip if moov is already at the front
    if not output_path:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "trace", "-i", video_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            combined = result.stdout + result.stderr
            moov_pos = combined.find("moov")
            mdat_pos = combined.find("mdat")
            if 0 < moov_pos < mdat_pos:
                print(f"[Faststart] Already optimised, skipping: {video_path}")
                return False
        except Exception:
            pass  # If probe fails, attempt remux anyway

    tmp_path = Path(tempfile.mktemp(suffix=".mp4", dir="/tmp"))
    try:
        if output_path:
            # ── Proxy mode: crushed 720p H.264 ──────────────────────────────
            # scale=-2:720  keeps the original aspect ratio for both landscape
            # (1920×1080 → 1280×720) and portrait (1080×1920 → 404×720).
            # -2 means FFmpeg auto-rounds the other dimension to be divisible
            # by 2, which H.264 requires.
            #
            # Timeout: veryfast 4K → roughly 3-4× realtime on CPU.
            # Give 5× duration + 120s buffer, with a 1800s (30 min) floor.
            encode_timeout = max(
                1800,
                int((video_duration or 0) * 5) + 120,
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-c:v", "libx264",
                "-crf", "28",
                "-preset", "veryfast",
                "-vf", "scale=-2:720",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(tmp_path),
            ]
            print(
                f"[Faststart] Encoding 720p proxy "
                f"(timeout {encode_timeout}s): {video_path}"
            )
        else:
            # ── In-place mode: stream copy, moov-first ───────────────────────
            # No re-encode; completes in <5s regardless of file size.
            encode_timeout = 300
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-c", "copy",
                "-movflags", "+faststart",
                str(tmp_path),
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=encode_timeout,
        )
        if result.returncode != 0:
            raise FFmpegError(f"faststart failed: {result.stderr[-500:]}")

        if output_path:
            dest = Path(output_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(tmp_path), str(dest))
            print(f"[Faststart] Proxy written: {output_path}")
        else:
            try:
                os.replace(str(tmp_path), str(path))
            except PermissionError:
                print(
                    f"[Faststart] Skipped (source mount is read-only): {video_path}\n"
                    f"  To enable in-place faststart, remove ':ro' from the "
                    f"MEDIA_SOURCE_PATH volume mount in docker-compose.yml"
                )
                return False
            print(f"[Faststart] MOOV atom moved to front: {video_path}")
        return True

    except subprocess.TimeoutExpired:
        raise FFmpegError(
            f"[Faststart] Timed out ({encode_timeout}s) on {video_path}"
        )
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def normalize_image(image_path: str, output_path: str, resolution: int = 224) -> str:
    """
    Normalize an image to a standard format and resolution.
    Handles HEIC and other formats.

    Args:
        image_path: Path to input image
        output_path: Path to save normalized image
        resolution: Target square resolution (default 224x224)

    Returns:
        Path to normalized image
    """
    from PIL import Image

    try:
        # Handle HEIC files
        if image_path.lower().endswith(".heic"):
            from pillow_heif import register_heic_opener

            register_heic_opener()

        img = Image.open(image_path).convert("RGB")

        # Resize with padding to maintain aspect ratio
        img.thumbnail((resolution, resolution), Image.Resampling.LANCZOS)
        new_img = Image.new("RGB", (resolution, resolution), color="black")
        offset = ((resolution - img.width) // 2, (resolution - img.height) // 2)
        new_img.paste(img, offset)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        new_img.save(output_path, "JPEG", quality=90)

        return output_path

    except Exception as e:
        raise FFmpegError(f"Image normalization failed for {image_path}: {str(e)}")
