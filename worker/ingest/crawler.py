"""
Media Crawler - Recursive directory scanning for media files
"""

import os
from pathlib import Path
from typing import List, Tuple

# Supported media extensions
SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".webp",
    ".bmp",
    ".gif",
}
SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".flv",
    ".wmv",
    ".webm",
    ".m4v",
}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS


def get_file_type(file_path: str) -> str:
    """Determine file type (image or video) based on extension"""
    ext = Path(file_path).suffix.lower()
    if ext in SUPPORTED_IMAGE_EXTENSIONS:
        return "image"
    elif ext in SUPPORTED_VIDEO_EXTENSIONS:
        return "video"
    return "unknown"


def crawl_media(media_root: str) -> List[Tuple[str, str]]:
    """
    Recursively crawl media_root for supported media files.
    Uses os.scandir for O(n) performance.

    Returns:
        List of tuples: (file_path, file_type)
    """
    if not os.path.exists(media_root):
        raise FileNotFoundError(f"Media root not found: {media_root}")

    results = []
    media_path = Path(media_root)

    def _scan_recursive(directory: Path):
        """Recursively scan directory"""
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False):
                        # Skip Mac OS artifact files and other junk
                        if entry.name.startswith("._") or entry.name in {".DS_Store", "Thumbs.db"}:
                            continue
                        file_ext = Path(entry.name).suffix.lower()
                        if file_ext in SUPPORTED_EXTENSIONS:
                            file_type = get_file_type(entry.path)
                            results.append((entry.path, file_type))
                    elif entry.is_dir(follow_symlinks=False):
                        # Skip hidden directories
                        if not entry.name.startswith("."):
                            _scan_recursive(Path(entry.path))
        except (PermissionError, OSError) as e:
            print(f"Warning: Could not access {directory}: {e}")

    _scan_recursive(media_path)
    return results


def count_media_by_type(media_root: str) -> dict:
    """Count media files by type"""
    files = crawl_media(media_root)
    counts = {"image": 0, "video": 0, "total": len(files)}
    for _, file_type in files:
        if file_type in counts:
            counts[file_type] += 1
    return counts
