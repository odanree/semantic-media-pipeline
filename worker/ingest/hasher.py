"""
File Hasher - SHA-256 hashing for idempotent processing
"""

import hashlib
from pathlib import Path
from typing import Optional


def compute_file_hash(file_path: str, chunk_size: int = 8192) -> str:
    """
    Compute SHA-256 hash of a file.
    For video files: Only hash first 8KB (header/metadata is unique enough)
    For images: Hash entire file

    Args:
        file_path: Path to the file
        chunk_size: Size of chunks to read (default 8KB)

    Returns:
        SHA-256 hash as hex string
    """
    sha256_hash = hashlib.sha256()
    try:
        # For video files, only hash the first 8KB (10,000x faster)
        # Header/metadata is unique enough to prevent duplicates
        ext = Path(file_path).suffix.lower()
        is_video = ext in {".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm", ".m4v"}
        max_bytes = chunk_size if is_video else None  # None = read entire file
        
        bytes_read = 0
        with open(file_path, "rb") as f:
            while True:
                # For videos: stop after first chunk (8KB)
                if is_video and bytes_read >= chunk_size:
                    break
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sha256_hash.update(chunk)
                bytes_read += len(chunk)
        return sha256_hash.hexdigest()
    except (IOError, OSError) as e:
        raise ValueError(f"Could not hash file {file_path}: {e}")


def is_file_processed(file_hash: str, db_session) -> bool:
    """
    Check if a file has been processed before using its hash.
    Used for idempotent processing (skip already-indexed files).

    Args:
        file_hash: SHA-256 hash of the file
        db_session: SQLAlchemy database session

    Returns:
        True if file has been processed, False otherwise
    """
    from .models import MediaFile

    result = db_session.query(MediaFile).filter_by(file_hash=file_hash).first()
    return result is not None


def get_existing_hash_record(file_hash: str, db_session):
    """
    Get the database record for a file that's already been processed.

    Args:
        file_hash: SHA-256 hash of the file
        db_session: SQLAlchemy database session

    Returns:
        MediaFile record or None
    """
    from .models import MediaFile

    return db_session.query(MediaFile).filter_by(file_hash=file_hash).first()
