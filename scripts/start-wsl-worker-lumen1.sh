#!/usr/bin/env bash
# ============================================================================
# Start WSL2 Native Celery Worker — lumen1 stack (GPU via DirectML)
# ============================================================================
# Prerequisites:
#   1. Run scripts/setup-wsl-worker.sh once first
#   2. lumen1 Redis on localhost:6379 and Postgres on localhost:5432
#      (docker-compose.yml updated with host port mappings, containers recreated)
#   3. Stop Docker worker: docker compose stop worker
#
# lumen1 source media is at /mnt/j/lumen-media but the DB stores paths as
# /mnt/source/... (the Docker container's mount path). This script bind-mounts
# /mnt/j/lumen-media at /mnt/source so the paths match.
#
# Usage:
#   bash scripts/start-wsl-worker-lumen1.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="$REPO_ROOT/worker"
VENV_DIR="$HOME/lumen-worker-venv"
ENV_FILE="$REPO_ROOT/.env.wsl-worker-lumen1"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: venv not found at $VENV_DIR"
    echo "Run: bash scripts/setup-wsl-worker.sh"
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: env file not found: $ENV_FILE"
    exit 1
fi

# ---------------------------------------------------------------------------
# Bind-mount J:\lumen-media at /mnt/source (matches Docker container's path)
# ---------------------------------------------------------------------------
if [ -d /mnt/j/lumen-media ]; then
    if ! mountpoint -q /mnt/source 2>/dev/null; then
        echo "==> Bind-mounting /mnt/j/lumen-media at /mnt/source..."
        # /mnt/source may be a dir with lumen2 symlinks — unmount first if needed
        # or overlay. Simplest: if it's a dir with no mounts, bind mount it.
        sudo mkdir -p /mnt/source
        # If /mnt/source/e symlink exists from lumen2 setup, that's inside the
        # original /mnt/source dir. After bind, we'd see J:\ instead.
        # Solution: use a separate mountpoint and symlink it.
        sudo mkdir -p /mnt/source-lumen1
        sudo mount --bind /mnt/j/lumen-media /mnt/source-lumen1
        # Now if lumen2 isn't active, we can also mount at /mnt/source directly
        # For safety, keep using /mnt/source-lumen1 and note the path issue.
        echo "      Mounted at /mnt/source-lumen1"
        echo "      WARNING: DB stores paths as /mnt/source/... not /mnt/source-lumen1/..."
        echo "      If lumen2 symlinks are not active on /mnt/source, run:"
        echo "        sudo mount --bind /mnt/j/lumen-media /mnt/source"
        echo "      This will make lumen1 DB paths resolve correctly."
    else
        echo "==> /mnt/source already mounted"
    fi
else
    echo "WARNING: /mnt/j/lumen-media not found — J: drive not mounted in WSL2"
    echo "Mount it with: sudo mkdir -p /mnt/j && sudo mount -t drvfs J: /mnt/j"
fi

echo ""
echo "==> Activating venv: $VENV_DIR"
source "$VENV_DIR/bin/activate"

echo "==> Loading env: $ENV_FILE"
set -a
eval "$(grep -v '^#' "$ENV_FILE" | grep -v '^$' | tr -d '\r')"
set +a

echo "==> DirectML check..."
python3 -c "
import os
print(f'   /dev/dxg present: {os.path.exists(\"/dev/dxg\")}')
try:
    import torch_directml
    d = torch_directml.device()
    import torch
    torch.zeros(1, device=d)
    print(f'   DirectML device: {d} [GPU ACTIVE]')
except Exception as e:
    print(f'   DirectML: {e} → will use CPU')
"

echo ""
echo "==> Starting Celery worker (lumen1, concurrency=${CELERY_CONCURRENCY:-2})..."
echo "    Broker: $CELERY_BROKER_URL"
echo "    Qdrant: $QDRANT_HOST:$QDRANT_PORT  collection=$QDRANT_COLLECTION_NAME"
echo "    Worker ID: ${WORKER_ID:-wsl-native-lumen1}"
echo ""
echo "    Stop Docker worker if you haven't already:"
echo "    docker compose stop worker"
echo ""

cd "$WORKER_DIR"

exec celery -A celery_app worker \
    --loglevel=info \
    --concurrency="${CELERY_CONCURRENCY:-2}" \
    --prefetch-multiplier="${CELERY_WORKER_PREFETCH_MULTIPLIER:-1}" \
    --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-50}" \
    --queues=celery,proxies \
    --hostname="${WORKER_HOSTNAME:-wsl-lumen1@%h}"
