"""
Media Opener — local HTTP server that opens files in mpv/VLC from the browser.

Usage:
    python scripts/vlc-opener.py

Listens on http://localhost:9876
Receives: GET /open?path=<encoded_container_path>&t=<seconds>

Path translation:
  Container paths (e.g. /mnt/source/...) are translated to Windows host
  paths using LUMEN_PATH_MAP_* env vars or the project .env file.
  Format: LUMEN_PATH_MAP_N=/container/prefix:C:/host/prefix

  Lumen1 default (from docker-compose.yml):
    /mnt/source  ->  J:/lumen-media

Player selection:
  mpv is preferred (handles HEVC natively). Falls back to VLC.
  Override with PLAYER_PATH env var or VLC_PATH for VLC specifically.
"""

import os
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Player location — mpv preferred, VLC fallback
# ---------------------------------------------------------------------------

MPV_CANDIDATES = [
    os.environ.get("PLAYER_PATH", ""),
    r"C:\Program Files\MPV Player\mpv.exe",
    r"C:\Program Files\mpv\mpv.exe",
    r"C:\Users\Danh\AppData\Local\Programs\mpv\mpv.exe",
    "mpv",
]

VLC_CANDIDATES = [
    os.environ.get("VLC_PATH", ""),
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    "vlc",
]


def _find_exe(candidates: list, name: str) -> str | None:
    for path in candidates:
        if not path:
            continue
        if os.path.isfile(path):
            return path
        if path == name:  # bare command — assume it's on PATH
            return path
    return None


def find_player() -> tuple[str, str]:
    """Return (exe_path, player_name). mpv preferred, VLC fallback."""
    mpv = _find_exe(MPV_CANDIDATES, "mpv")
    if mpv:
        return mpv, "mpv"
    vlc = _find_exe(VLC_CANDIDATES, "vlc")
    if vlc:
        return vlc, "vlc"
    raise FileNotFoundError(
        "No media player found. Install mpv or VLC, or set PLAYER_PATH."
    )


# ---------------------------------------------------------------------------
# Path map: container linux path → Windows host path
# ---------------------------------------------------------------------------

def _load_env_file() -> dict:
    """Load key=value pairs from the project .env file (best-effort)."""
    env_path = Path(__file__).parent.parent / ".env"
    result = {}
    if not env_path.is_file():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip()
    return result


def _build_path_maps() -> list[tuple[str, str]]:
    """
    Collect LUMEN_PATH_MAP_* from environment + .env file.
    Each entry is (linux_prefix, windows_prefix), sorted longest-prefix first.
    Lumen1 default (/mnt/source → J:/lumen-media) is always included as fallback.
    """
    raw: dict[str, str] = {}

    # 1. Start with lumen1 default from docker-compose
    raw["LUMEN_PATH_MAP_DEFAULT"] = "/mnt/source:J:/lumen-media"

    # 2. Overlay with .env file values
    raw.update(_load_env_file())

    # 3. Overlay with live environment variables (highest priority)
    for k, v in os.environ.items():
        if k.startswith("LUMEN_PATH_MAP"):
            raw[k] = v

    maps = []
    for k, v in raw.items():
        if not re.match(r"LUMEN_PATH_MAP", k):
            continue
        if ":" not in v:
            continue
        # Format: /linux/prefix:C:/windows/prefix
        # Split on first colon that follows a linux path segment
        # e.g. "/mnt/source:J:/lumen-media"  →  ("/mnt/source", "J:/lumen-media")
        parts = v.split(":", 1)
        linux_prefix = parts[0].rstrip("/")
        win_prefix = parts[1]
        maps.append((linux_prefix, win_prefix))

    # Longest linux prefix first so more-specific mounts win
    maps.sort(key=lambda m: len(m[0]), reverse=True)
    return maps


PATH_MAPS = _build_path_maps()


def translate_path(container_path: str) -> str:
    """
    Translate a container Linux path to a Windows host path.
    Falls back to the original path if no map matches.
    """
    for linux_prefix, win_prefix in PATH_MAPS:
        if container_path.startswith(linux_prefix + "/") or container_path == linux_prefix:
            remainder = container_path[len(linux_prefix):]
            # Normalise to forward slashes throughout — Windows and VLC both handle them
            translated = win_prefix.rstrip("/") + remainder
            print(f"[vlc-opener] Mapped:  {container_path}")
            print(f"[vlc-opener]      ->  {translated}")
            return translated
    print(f"[vlc-opener] No path map matched, using as-is: {container_path}")
    return container_path


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default access log noise

    def do_GET(self):
        if not self.path.startswith("/open"):
            self._respond(404, "Not found")
            return

        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        container_path = params.get("path", [None])[0]
        if not container_path:
            self._respond(400, "Missing path parameter")
            return

        timestamp = params.get("t", ["0"])[0]
        try:
            t = float(timestamp)
        except ValueError:
            t = 0.0

        host_path = translate_path(container_path)
        print(f"[vlc-opener] Opening: {host_path} @ {t:.1f}s")

        try:
            player, player_name = find_player()
        except FileNotFoundError as e:
            print(f"[vlc-opener] ERROR: {e}")
            self._respond(500, str(e))
            return

        if player_name == "mpv":
            # mpv: --start handles sub-second seeks and short clips cleanly
            cmd = [player, f"--start={t:.3f}", host_path]
        else:
            # VLC fallback: skip --start-time for clips < 1s (seeks past end)
            cmd = [player, "--no-video-title-show", "--avcodec-hw=none"]
            if t >= 1.0:
                cmd.append(f"--start-time={t:.3f}")
            cmd.append(host_path)
        print(f"[vlc-opener] CMD: {' '.join(cmd)}")
        try:
            subprocess.Popen(cmd)
            self._respond(200, "OK")
        except Exception as e:
            print(f"[vlc-opener] Failed to launch VLC: {e}")
            self._respond(500, str(e))

    def _respond(self, status: int, body: str):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(encoded)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        _player_exe, _player_name = find_player()
        print(f"[vlc-opener] Player: {_player_name} ({_player_exe})")
    except FileNotFoundError as e:
        print(f"[vlc-opener] WARNING: {e}")

    print("[vlc-opener] Path maps loaded:")
    for lp, wp in PATH_MAPS:
        print(f"  {lp}  ->  {wp}")

    port = int(os.environ.get("VLC_OPENER_PORT", "9876"))
    server = HTTPServer(("localhost", port), Handler)
    print(f"\n[vlc-opener] Listening on http://localhost:{port}")
    print("[vlc-opener] Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[vlc-opener] Stopped")
