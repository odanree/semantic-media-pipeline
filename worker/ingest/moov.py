"""
MP4 moov atom extraction and sidecar generation.

Extracts the moov atom from a non-faststart MP4/MOV file and saves it as a
tiny sidecar with corrected chunk offsets. The sidecar can be prepended
before the original file's mdat data to present a virtual faststart file to
ffmpeg/ffprobe without duplicating the video data.

Virtual faststart layout (served by api via HTTP range requests):
  [corrected moov]  — bytes 0 … moov_size-1  (local sidecar, fast)
  [original mdat…]  — bytes moov_size … N     (9P file, random-access pread)

Chunk offset correction:
  Original offsets point to absolute positions in the source file.
  In the virtual file, moov is prepended before mdat, so every chunk at
  original offset X is now at: moov_size + (X - mdat_offset).
  adjustment = moov_size - mdat_offset
"""

import json
import os
import struct
from pathlib import Path
from typing import Optional

# Container box types — we recurse into these when walking moov
_CONTAINER_TYPES = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl",
    b"edts", b"udta", b"meta", b"ilst", b"dinf",
}

# Maximum moov size we'll read into memory (200 MB is generous)
_MAX_MOOV_BYTES = 200 * 1024 * 1024


def _read_box_at(f, offset: int, file_size: int):
    """
    Read box header at *offset* from open file *f*.
    Returns (size, box_type_bytes, header_len) or (0, b'', 0) on error.
    """
    if offset + 8 > file_size:
        return 0, b"", 0
    f.seek(offset)
    raw = f.read(16)
    if len(raw) < 8:
        return 0, b"", 0
    size = struct.unpack_from(">I", raw, 0)[0]
    btype = raw[4:8]
    header_len = 8
    if size == 1:
        if len(raw) < 16:
            return 0, b"", 0
        size = struct.unpack_from(">Q", raw, 8)[0]
        header_len = 16
    elif size == 0:
        size = file_size - offset
    return size, btype, header_len


def _correct_offsets(moov: bytearray, adjustment: int) -> None:
    """
    Walk *moov* in-place and add *adjustment* to every stco/co64 entry.
    adjustment = moov_size - mdat_offset
    """
    def _walk(offset: int, end: int) -> None:
        while offset + 8 <= end and offset + 8 <= len(moov):
            size = struct.unpack_from(">I", moov, offset)[0]
            btype = bytes(moov[offset + 4 : offset + 8])
            header_len = 8
            if size == 1:
                if offset + 16 > len(moov):
                    break
                size = struct.unpack_from(">Q", moov, offset + 8)[0]
                header_len = 16
            elif size == 0:
                size = end - offset
            if size < 8:
                break
            box_end = min(offset + size, end)
            content = offset + header_len

            if btype == b"stco":
                # version(1) flags(3) entry_count(4) entries(4 each)
                if content + 8 > len(moov):
                    offset = box_end
                    continue
                count = struct.unpack_from(">I", moov, content + 4)[0]
                for i in range(count):
                    pos = content + 8 + i * 4
                    if pos + 4 > len(moov):
                        break
                    val = struct.unpack_from(">I", moov, pos)[0]
                    struct.pack_into(">I", moov, pos, val + adjustment)

            elif btype == b"co64":
                if content + 8 > len(moov):
                    offset = box_end
                    continue
                count = struct.unpack_from(">I", moov, content + 4)[0]
                for i in range(count):
                    pos = content + 8 + i * 8
                    if pos + 8 > len(moov):
                        break
                    val = struct.unpack_from(">Q", moov, pos)[0]
                    struct.pack_into(">Q", moov, pos, val + adjustment)

            elif btype in _CONTAINER_TYPES:
                _walk(content, box_end)

            offset = box_end

    _walk(0, len(moov))


def extract_moov_sidecar(video_path: str, sidecar_path: str) -> bool:
    """
    Extract the moov atom from a non-faststart MP4/MOV and write a sidecar pair:
      sidecar_path           — corrected moov bytes (raw)
      sidecar_path + '.json' — {"mdat_offset": N, "moov_size": N, "file_size": N}

    Returns True  if sidecar was written (non-faststart file, moov found).
    Returns False if file is already faststart, moov not found, or unsupported.
    """
    try:
        file_size = os.path.getsize(video_path)
    except OSError as e:
        print(f"[MoovSidecar] Cannot stat {video_path}: {e}")
        return False

    moov_data: Optional[bytes] = None
    mdat_offset: Optional[int] = None
    is_faststart = False

    try:
        with open(video_path, "rb") as f:
            offset = 0
            while offset < file_size - 7:
                size, btype, header_len = _read_box_at(f, offset, file_size)
                if size < 8:
                    break

                if btype == b"mdat":
                    if mdat_offset is None:
                        mdat_offset = offset
                    if moov_data is not None:
                        # moov appeared before mdat — already faststart
                        is_faststart = True
                        break

                elif btype == b"moov":
                    if size > _MAX_MOOV_BYTES:
                        print(
                            f"[MoovSidecar] moov too large "
                            f"({size // 1024 // 1024} MB), skipping: {video_path}"
                        )
                        return False
                    f.seek(offset)
                    moov_data = f.read(size)
                    if mdat_offset is not None:
                        # mdat appeared before moov — non-faststart ✓
                        break
                    # moov before mdat — keep scanning for mdat to confirm

                offset += size

    except OSError as e:
        print(f"[MoovSidecar] Read error for {video_path}: {e}")
        return False

    if is_faststart or (moov_data is not None and mdat_offset is None):
        print(f"[MoovSidecar] Already faststart, skipping: {video_path}")
        return False

    if moov_data is None:
        print(f"[MoovSidecar] moov atom not found in {video_path}")
        return False

    if mdat_offset is None:
        print(f"[MoovSidecar] mdat not found in {video_path}")
        return False

    # Correct chunk offsets for virtual faststart layout
    moov_arr = bytearray(moov_data)
    adjustment = len(moov_data) - mdat_offset
    _correct_offsets(moov_arr, adjustment)

    # Write sidecar
    try:
        Path(sidecar_path).parent.mkdir(parents=True, exist_ok=True)
        with open(sidecar_path, "wb") as f:
            f.write(moov_arr)
        with open(sidecar_path + ".json", "w") as f:
            json.dump(
                {
                    "mdat_offset": mdat_offset,
                    "moov_size": len(moov_data),
                    "file_size": file_size,
                },
                f,
            )
    except OSError as e:
        print(f"[MoovSidecar] Write error for {sidecar_path}: {e}")
        return False

    print(
        f"[MoovSidecar] Written ({len(moov_arr) // 1024} KB, "
        f"mdat_offset={mdat_offset}): {sidecar_path}"
    )
    return True
