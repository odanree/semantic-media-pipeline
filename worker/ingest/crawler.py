"""
Media Crawler - Recursive directory scanning for media files
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# Well-known Windows/macOS system directories that should never be crawled.
# These appear on any NTFS drive and contain no user media.
_SYSTEM_DIR_NAMES: Set[str] = {
    "$RECYCLE.BIN",
    "System Volume Information",
    "$SysReset",
    "Recovery",
    "Config.Msi",
    ".Spotlight-V100",
    ".Trashes",
    ".fseventsd",
}


def _build_exclude_inodes(paths: List[str]) -> Set[Tuple[int, int]]:
    """
    Return a set of (dev, ino) pairs for each path that exists.
    Used for bind-mount-safe exclusion: /mnt/source/frame_cache and
    /mnt/frame_cache have identical inodes even though they're different
    container paths — os.path.samefile / os.stat correctly detects this.
    """
    inodes: Set[Tuple[int, int]] = set()
    for p in paths:
        if not p:
            continue
        try:
            st = os.stat(p)
            inodes.add((st.st_dev, st.st_ino))
            log.debug("Crawler: excluding path %s (dev=%d ino=%d)", p, st.st_dev, st.st_ino)
        except OSError:
            pass  # path doesn't exist yet — ignore
    return inodes


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


def crawl_media(
    media_root: str,
    extra_exclude_paths: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """
    Recursively crawl media_root for supported media files.
    Uses os.scandir for O(n) performance.

    Directories are excluded if they match any of:
      1. Well-known OS system directories (_SYSTEM_DIR_NAMES).
      2. Hidden directories (name starts with ".").
      3. FRAME_CACHE_DIR env var — prevents re-ingesting cached frames when
         the frame cache lives on the same drive as the media source.
         Detection uses inode comparison so bind-mount aliases
         (/mnt/source/frame_cache vs /mnt/frame_cache) are caught correctly.
      4. CRAWL_EXCLUDE_DIRS env var — comma-separated additional paths.
      5. extra_exclude_paths argument passed by the caller.

    Returns:
        List of tuples: (file_path, file_type)
    """
    if not os.path.exists(media_root):
        raise FileNotFoundError(f"Media root not found: {media_root}")

    # Build the set of (dev, ino) pairs for all directories to exclude.
    exclude_paths: List[str] = []

    # Auto-exclude FRAME_CACHE_DIR (the most common overlap risk)
    frame_cache_dir = os.getenv("FRAME_CACHE_DIR", "").strip()
    if frame_cache_dir:
        exclude_paths.append(frame_cache_dir)

    # User-defined extra exclusions from env (comma-separated)
    crawl_exclude_env = os.getenv("CRAWL_EXCLUDE_DIRS", "").strip()
    if crawl_exclude_env:
        exclude_paths.extend(p.strip() for p in crawl_exclude_env.split(",") if p.strip())

    if extra_exclude_paths:
        exclude_paths.extend(extra_exclude_paths)

    exclude_inodes = _build_exclude_inodes(exclude_paths)

    if exclude_inodes:
        log.info("Crawler: %d directory exclusion(s) active", len(exclude_inodes))

    results = []
    media_path = Path(media_root)

    def _is_excluded_dir(entry_path: str) -> bool:
        """Return True if this directory should be skipped."""
        try:
            st = os.stat(entry_path)
            return (st.st_dev, st.st_ino) in exclude_inodes
        except OSError:
            return False

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
                        # Skip hidden directories and known OS system dirs
                        if entry.name.startswith(".") or entry.name in _SYSTEM_DIR_NAMES:
                            continue
                        # Skip any inode-matched exclusion (catches bind-mount aliases)
                        if _is_excluded_dir(entry.path):
                            log.info("Crawler: skipping excluded dir %s", entry.path)
                            continue
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
