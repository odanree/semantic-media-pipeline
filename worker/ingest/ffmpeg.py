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
    timeout: int = 600,
) -> List[str]:
    """
    Extract keyframes from a video at specified FPS and resolution.
    Uses FFmpeg to extract frames and scales to resolution×resolution.

    Args:
        video_path: Path to input video
        output_dir: Directory to save extracted JPEG frames
        fps: Frames per second to extract (default 0.5 = 1 frame per 2 seconds)
        resolution: Target square resolution for CLIP (default 224x224)
        timeout: Timeout in seconds (default 600s)

    Returns:
        List of paths to extracted frames
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

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
        raise FFmpegError(f"FFmpeg timeout extracting frames from {video_path}")
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
