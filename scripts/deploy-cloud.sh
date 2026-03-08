#!/bin/bash
# =============================================================================
# Lumen Cloud Deploy Script
# Target: Hetzner CX32 (8GB / 4 vCPU) — Ubuntu 24.04
# Domain: lumen.danhle.net
# Storage: Cloudflare R2
#
# Usage:
#   1. SSH into fresh server: ssh root@<server-ip>
#   2. curl -fsSL https://raw.githubusercontent.com/odanree/semantic-media-pipeline/main/scripts/deploy-cloud.sh | bash
#   OR copy this file and run: bash deploy-cloud.sh
# =============================================================================

set -euo pipefail

DOMAIN="lumen.danhle.net"
REPO="https://github.com/odanree/semantic-media-pipeline.git"
APP_DIR="/opt/lumen"

echo "============================================================"
echo " Lumen Cloud Deploy — $DOMAIN"
echo "============================================================"

# -----------------------------------------------------------------------------
# 1. System updates + essentials
# -----------------------------------------------------------------------------
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq git curl wget unzip ca-certificates gnupg

# -----------------------------------------------------------------------------
# 2. Swap (2GB) — Qdrant needs breathing room
# -----------------------------------------------------------------------------
echo "[2/7] Configuring swap..."
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "vm.swappiness=10" >> /etc/sysctl.conf
  sysctl -p
  echo "  Swap: 2GB created"
else
  echo "  Swap: already exists, skipping"
fi

# -----------------------------------------------------------------------------
# 3. Docker
# -----------------------------------------------------------------------------
echo "[3/7] Installing Docker..."
if ! command -v docker &>/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable docker
  systemctl start docker
  echo "  Docker: installed"
else
  echo "  Docker: already installed"
fi

# -----------------------------------------------------------------------------
# 4. Clone repo
# -----------------------------------------------------------------------------
echo "[4/7] Cloning repository..."
if [ -d "$APP_DIR" ]; then
  echo "  $APP_DIR exists — pulling latest..."
  cd "$APP_DIR" && git pull
else
  git clone "$REPO" "$APP_DIR"
  echo "  Cloned to $APP_DIR"
fi
cd "$APP_DIR"

# -----------------------------------------------------------------------------
# 5. Create production .env
# -----------------------------------------------------------------------------
echo "[5/7] Writing production .env..."

# Prompt for secrets if not already set
if [ ! -f "$APP_DIR/.env" ]; then
  cat > "$APP_DIR/.env" << 'EOF'
# ============================================================================
# Lumen Production — Hetzner CX32
# ============================================================================
COMPOSE_INTERACTIVE_NO_CLI=1

# Celery / Redis
CELERY_BROKER_URL=redis://lumen-redis:6379/0
CELERY_RESULT_BACKEND=redis://lumen-redis:6379/0
REDIS_HOST=lumen-redis
REDIS_PORT=6379
REDIS_URL=redis://lumen-redis:6379

# Qdrant
QDRANT_HOST=lumen-qdrant
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_PREFER_GRPC=true
QDRANT_COLLECTION_NAME=media_vectors

# PostgreSQL
DATABASE_HOST=lumen-postgres
DATABASE_PORT=5432
DATABASE_NAME=lumen
DATABASE_USER=lumen_user
DATABASE_PASSWORD=lumen_secure_password_2026
DATABASE_URL=postgresql://lumen_user:lumen_secure_password_2026@lumen-postgres:5432/lumen
DATABASE_ASYNC_URL=postgresql+asyncpg://lumen_user:lumen_secure_password_2026@lumen-postgres:5432/lumen

# Media — no local mount needed (R2 is the source)
MEDIA_SOURCE_PATH=/tmp/empty
FRAME_CACHE_DIR=/tmp/frame_cache
MEDIA_ROOT=/opt/lumen/data/media

# Storage: Cloudflare R2
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://3654d8cd037076de8e1e9ce41cd13707.r2.cloudflarestorage.com
S3_BUCKET=lumen-media-demo
S3_ACCESS_KEY=9b4b2af77b732e2ed896b323236cbd65
S3_SECRET_KEY=05e783cd5ec1584256fdf8aaf8c800d2447723276a3b52e9f8571017c94f34ab
S3_REGION=auto

# CLIP
CLIP_MODEL_NAME=clip-ViT-L-14
EMBEDDING_BATCH_SIZE=16
EMBEDDING_DEVICE=cpu
KEYFRAME_FPS=0.5
KEYFRAME_RESOLUTION=224
FFMPEG_TIMEOUT=86400

# API
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=false
API_KEY_REQUIRED=false
API_KEY=
LOG_LEVEL=info

# Rate limiting
RATE_LIMIT_SEARCH=30/minute
RATE_LIMIT_SEARCH_VEC=60/minute
RATE_LIMIT_STREAM=60/minute
RATE_LIMIT_THUMBNAIL=120/minute
RATE_LIMIT_DEFAULT=200/minute

# Frontend — points to the public domain
NEXT_PUBLIC_API_URL=https://lumen.danhle.net/api
NEXT_PUBLIC_QDRANT_HOST=lumen-qdrant
NEXT_PUBLIC_QDRANT_PORT=6333

# Observability
FLOWER_PORT=5555
FLOWER_BROKER=redis://lumen-redis:6379/0
PROMETHEUS_ENABLED=false

# GPU — CPU only on cloud
USE_CUDA=false
CUDA_VISIBLE_DEVICES=
HIP_VISIBLE_DEVICES=

# Pexels
PEXELS_API_KEY=ZRWRgCcjTv1VVBLpFjcKMvWSDM17tdh0F4CxcQcc9dlMNxNayzwXbqei

# Env
DEBUG=false
ENVIRONMENT=production
EOF
  echo "  .env written"
else
  echo "  .env already exists — skipping (edit manually if needed)"
fi

# Create required dirs (bind mounts must exist even if R2 is used)
mkdir -p "$APP_DIR/data/media"
mkdir -p /tmp/empty        # MEDIA_SOURCE_PATH — empty, worker reads from R2 not filesystem
mkdir -p /tmp/frame_cache  # FRAME_CACHE_DIR — writable cache for extracted frames

# -----------------------------------------------------------------------------
# 6. Caddy (reverse proxy + auto SSL)
# -----------------------------------------------------------------------------
echo "[6/7] Installing Caddy..."
if ! command -v caddy &>/dev/null; then
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
  echo "  Caddy: installed"
else
  echo "  Caddy: already installed"
fi

cat > /etc/caddy/Caddyfile << EOF
lumen.danhle.net {
    # Forward /api/* to FastAPI
    handle /api/* {
        reverse_proxy localhost:8000
    }

    # Forward everything else to Next.js frontend
    handle {
        reverse_proxy localhost:3000
    }

    # Security headers
    header {
        X-Frame-Options DENY
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
    }
}
EOF

systemctl enable caddy
systemctl restart caddy
echo "  Caddy: configured for $DOMAIN"

# -----------------------------------------------------------------------------
# 7. Start services
# -----------------------------------------------------------------------------
echo "[7/7] Starting Lumen services..."
cd "$APP_DIR"

# Build and start (no MinIO needed on cloud — R2 is remote)
docker compose up -d --build \
  redis postgres qdrant api worker frontend flower

echo ""
echo "============================================================"
echo " Deploy complete!"
echo "============================================================"
echo ""
echo " Services running:"
docker compose ps --format "table {{.Name}}\t{{.Status}}"
echo ""
echo " Next steps:"
echo "  1. Point DNS: lumen.danhle.net → $(curl -s ifconfig.me) (A record in Cloudflare)"
echo "  2. Wait ~60s for SSL cert to provision"
echo "  3. Trigger ingest from R2:"
echo ""
echo "     curl -X POST https://lumen.danhle.net/api/ingest \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"media_root\": \"\"}'"
echo ""
echo "  4. Monitor: https://lumen.danhle.net"
echo "  5. Flower:  http://$(curl -s ifconfig.me):5555"
echo ""
