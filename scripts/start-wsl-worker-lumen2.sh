#!/usr/bin/env bash
# ============================================================================
# Start WSL2 Native Celery Worker — lumen2 stack (GPU via DirectML)
# ============================================================================
# Prerequisites:
#   1. Run scripts/setup-wsl-worker.sh once first
#   2. Docker lumen2 stack running (redis2, postgres2, qdrant2, api2, flower2)
#   3. Stop Docker worker2: docker compose -f docker-compose.second.yml -p lumen2 stop worker2
#
# Usage:
#   bash scripts/start-wsl-worker-lumen2.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="$REPO_ROOT/worker"
VENV_DIR="$HOME/lumen-worker-venv"
ENV_FILE="$REPO_ROOT/.env.wsl-worker-lumen2"

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

# Check Redis is reachable (lumen2 Redis on port 6380)
if ! redis-cli -p 6380 ping &>/dev/null 2>&1; then
    # redis-cli may not be installed; fall back to nc
    if command -v nc &>/dev/null; then
        if ! nc -z localhost 6380 &>/dev/null 2>&1; then
            echo "ERROR: Cannot reach Redis on localhost:6380"
            echo "Is the lumen2 Docker stack running? Try: docker compose -f docker-compose.second.yml -p lumen2 ps"
            exit 1
        fi
    fi
fi

echo "==> Activating venv: $VENV_DIR"
source "$VENV_DIR/bin/activate"

echo "==> Loading env: $ENV_FILE"
# Export all vars from env file (skip blank lines and comments)
set -a
# Expand $USER in MEDIA_ROOT
eval "$(grep -v '^#' "$ENV_FILE" | grep -v '^$' | tr -d '\r' | sed 's/\$USER/'"$USER"'/g')"
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
echo "==> Starting Celery worker (lumen2, concurrency=${CELERY_CONCURRENCY:-2})..."
echo "    Broker: $CELERY_BROKER_URL"
echo "    Qdrant: $QDRANT_HOST:$QDRANT_PORT  collection=$QDRANT_COLLECTION_NAME"
echo "    Worker ID: ${WORKER_ID:-wsl-native-lumen2}"
echo ""
echo "    Stop Docker worker2 if you haven't already:"
echo "    docker compose -f docker-compose.second.yml -p lumen2 stop worker2"
echo ""

cd "$WORKER_DIR"

exec celery -A celery_app worker \
    --loglevel=info \
    --concurrency="${CELERY_CONCURRENCY:-2}" \
    --prefetch-multiplier="${CELERY_WORKER_PREFETCH_MULTIPLIER:-1}" \
    --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-50}" \
    --queues=celery,proxies \
    --hostname="${WORKER_HOSTNAME:-wsl-lumen2@%h}"
