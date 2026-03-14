#!/usr/bin/env bash
# ============================================================================
# WSL2 Native Celery Worker — One-Time Setup
# ============================================================================
# Run once inside WSL2 to install Python, deps, and configure symlinks.
# After this, use start-wsl-worker-lumen1.sh or start-wsl-worker-lumen2.sh
# to launch the actual worker.
#
# Usage:
#   bash scripts/setup-wsl-worker.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$HOME/lumen-worker-venv"

echo "==> Repo: $REPO_ROOT"
echo "==> Venv: $VENV_DIR"
echo ""

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
echo "[1/6] Installing system packages..."
sudo apt-get update -q
sudo apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-venv \
    python3.10-dev \
    python3-pip \
    build-essential \
    git \
    curl \
    ffmpeg \
    libgl1 \
    libglib2.0-0

echo "      Python: $(python3.10 --version)"
echo "      FFmpeg: $(ffmpeg -version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# 2. Python virtual environment
# ---------------------------------------------------------------------------
echo ""
echo "[2/6] Creating virtual environment at $VENV_DIR ..."
if [ -d "$VENV_DIR" ]; then
    echo "      Already exists — skipping creation"
else
    python3.10 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install -U pip setuptools wheel -q

# ---------------------------------------------------------------------------
# 3. Install requirements
# ---------------------------------------------------------------------------
echo ""
echo "[3/6] Installing Python requirements (this may take 10-15 min on first run)..."
echo "      torch + sentence-transformers + audio deps..."
pip install -r "$REPO_ROOT/worker/requirements.txt"

# torch-directml is guarded by sys_platform == "win32" in requirements.txt,
# which skips it on WSL2 Linux. Install it explicitly here.
echo ""
echo "      Installing torch-directml for WSL2 DirectX GPU access..."
pip install torch-directml==0.2.5.dev240914

echo "      All requirements installed."

# ---------------------------------------------------------------------------
# 4. Verify DirectML + /dev/dxg
# ---------------------------------------------------------------------------
echo ""
echo "[4/6] Verifying DirectML and GPU access..."
if [ -e /dev/dxg ]; then
    echo "      /dev/dxg: FOUND ✓ (DirectML can access GPU)"
else
    echo "      /dev/dxg: NOT FOUND ✗"
    echo "      This means DirectML cannot see your GPU in this WSL2 session."
    echo "      Required: Windows 11, WSL2 kernel >= 5.15, AMD drivers installed on Windows."
    echo "      Worker will fall back to CPU until /dev/dxg is available."
fi

python3.10 -c "
try:
    import torch_directml
    d = torch_directml.device()
    import torch
    torch.zeros(1, device=d)
    print(f'      DirectML device: {d} ✓')
except Exception as e:
    print(f'      DirectML: not working ({e})')
    print('      Will use CPU fallback.')
"

# ---------------------------------------------------------------------------
# 5. Symlinks — /mnt/source paths
# ---------------------------------------------------------------------------
echo ""
echo "[5/6] Setting up /mnt/source symlinks..."

# Create /mnt/source as a directory (holds both lumen1 and lumen2 sub-paths)
sudo mkdir -p /mnt/source

# lumen2 paths:
#   /mnt/source/e           → E:\Unsorted
#   /mnt/source/f-downloads → C:\Users\<user>\Downloads\<media-folder>
if [ -d /mnt/e/Unsorted ]; then
    if [ ! -e /mnt/source/e ]; then
        sudo ln -s /mnt/e/Unsorted /mnt/source/e
        echo "      Created: /mnt/source/e -> /mnt/e/Unsorted"
    else
        echo "      Exists:  /mnt/source/e"
    fi
else
    echo "      WARNING: /mnt/e/Unsorted not found — E: drive not mounted in WSL2?"
    echo "      Mount it with: sudo mkdir -p /mnt/e && sudo mount -t drvfs E: /mnt/e"
fi

MEDIA_SOURCE_PATH="/mnt/c/Users/<user>/Downloads/<media-folder>"
if [ -d "$MEDIA_SOURCE_PATH" ]; then
    if [ ! -e /mnt/source/f-downloads ]; then
        sudo ln -s "$MEDIA_SOURCE_PATH" /mnt/source/f-downloads
        echo "      Created: /mnt/source/f-downloads -> $MEDIA_SOURCE_PATH"
    else
        echo "      Exists:  /mnt/source/f-downloads"
    fi
else
    echo "      WARNING: '$MEDIA_SOURCE_PATH' not found — path may differ"
fi

# lumen1 paths:
#   /mnt/source (entire J:\lumen-media must appear here)
#   But /mnt/source is already a dir with lumen2 links.
#   For lumen1, we use /mnt/source-lumen1 → /mnt/j/lumen-media
#   and set MEDIA_SOURCE_ROOT env in the start script.
#   (lumen1 DB paths use /mnt/source/... so this doesn't match directly.
#    lumen1 backfill runs in Docker container which has the correct mount.
#    Native lumen1 worker needs a separate bind mount — see start-wsl-worker-lumen1.sh)

# frame cache for lumen1
if [ -d /mnt/j/frame_cache ]; then
    sudo mkdir -p /mnt/frame_cache
    if ! mountpoint -q /mnt/frame_cache 2>/dev/null; then
        sudo mount --bind /mnt/j/frame_cache /mnt/frame_cache
        echo "      Bind-mounted: /mnt/frame_cache -> /mnt/j/frame_cache"
        echo "      NOTE: Add to /etc/fstab for persistence across WSL2 restarts:"
        echo "        /mnt/j/frame_cache /mnt/frame_cache none bind 0 0"
    else
        echo "      Exists:  /mnt/frame_cache (already mounted)"
    fi
else
    echo "      /mnt/j/frame_cache not found — J: drive not mounted? Skipping frame cache bind."
fi

# ---------------------------------------------------------------------------
# 6. MEDIA_ROOT dir for lumen2
# ---------------------------------------------------------------------------
echo ""
echo "[6/6] Creating lumen2 MEDIA_ROOT and frame cache..."
mkdir -p "$HOME/lumen2-media"
mkdir -p /tmp/lumen2-proxies
# Frame cache on I: drive (Sabrent V2 NVMe)
if [ -d /mnt/i ]; then
    mkdir -p /mnt/i/lumen2-frame-cache
    echo "      FRAME_CACHE_DIR: /mnt/i/lumen2-frame-cache"
else
    echo "      WARNING: /mnt/i not found — mount I: drive first:"
    echo "        sudo mkdir -p /mnt/i && sudo mount -t drvfs I: /mnt/i"
    echo "      Then: mkdir -p /mnt/i/lumen2-frame-cache"
fi
echo "      MEDIA_ROOT: $HOME/lumen2-media"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Setup complete!"
echo ""
echo " To start the lumen2 GPU worker:"
echo "   bash $REPO_ROOT/scripts/start-wsl-worker-lumen2.sh"
echo ""
echo " To start the lumen1 GPU worker (after exposing Redis/Postgres ports):"
echo "   bash $REPO_ROOT/scripts/start-wsl-worker-lumen1.sh"
echo ""
echo " Then stop the corresponding Docker worker:"
echo "   lumen2: docker compose -f docker-compose.second.yml -p lumen2 stop worker2"
echo "   lumen1: docker compose stop worker"
echo "============================================================"
