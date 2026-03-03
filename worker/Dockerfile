# syntax=docker/dockerfile:1
# ============================================================================
# Worker Dockerfile - Celery Worker with DirectML GPU Acceleration
# ============================================================================
# Uses torch-directml for GPU acceleration via /dev/dxg on WSL2.
# This avoids the ROCm KFD driver requirement that is missing from the
# standard Microsoft WSL2 kernel, using DirectX passthrough instead.
#
# Supports: AMD Radeon RX 6000/7000, Intel Arc, NVIDIA (via DirectX 12)
# ============================================================================

FROM python:3.10-slim

# System deps layer (cached unless Dockerfile changes)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps layer (cached until requirements.txt changes)
COPY worker/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U pip setuptools wheel && \
    pip install -r requirements.txt

# Code layer (rebuilds only when source changes)
COPY worker/ .

# Copy test scripts from repo root
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# WSL2: DirectML needs libd3d12.so from the host-mapped driver path
ENV LD_LIBRARY_PATH=/usr/lib/wsl/lib:${LD_LIBRARY_PATH}

EXPOSE 5555

CMD ["celery", "-A", "celery_app", "worker", "--loglevel=info"]
