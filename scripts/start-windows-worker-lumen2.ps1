# ============================================================================
# start-windows-worker-lumen2.ps1
# Start the Windows-native Celery worker for lumen2 with CUDA GPU.
# ============================================================================

$ErrorActionPreference = "Stop"

$VENV_DIR  = "C:\lumen-worker-venv"
$PROJ_DIR  = (Resolve-Path "$PSScriptRoot\..").Path
$ENV_FILE  = "$PROJ_DIR\.env.windows-worker-lumen2"
$WORKER_DIR = "$PROJ_DIR\worker"

# ----------------------------------------------------------------------------
# 1. Load env file into the current process environment
# ----------------------------------------------------------------------------
Write-Host "==> Loading env: $ENV_FILE"
Get-Content $ENV_FILE | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } | ForEach-Object {
    $parts = $_ -split '=', 2
    $name  = $parts[0].Trim()
    $value = $parts[1].Trim()
    [System.Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

# ----------------------------------------------------------------------------
# 2. Test CUDA (GPU 1)
# ----------------------------------------------------------------------------
Write-Host "==> Testing CUDA (GPU 1)..."
$cudaTest = & "$VENV_DIR\Scripts\python.exe" -c @"
import torch
if torch.cuda.is_available() and torch.cuda.device_count() > 1:
    print(f'   GPU 1 OK: {torch.cuda.get_device_name(1)} [CUDA ACTIVE]')
elif torch.cuda.is_available():
    print(f'   Only 1 GPU found: {torch.cuda.get_device_name(0)} — check if lumen2 should use cuda:0')
else:
    print('   CUDA not available — will fall back to CPU')
"@
Write-Host $cudaTest

# ----------------------------------------------------------------------------
# 3. Start Celery worker
# ----------------------------------------------------------------------------
$concurrency = if ($env:CELERY_CONCURRENCY) { $env:CELERY_CONCURRENCY } else { "2" }
$instance    = Get-Random -Minimum 1 -Maximum 9999
$hostname    = "win-lumen2-$instance@$env:COMPUTERNAME"

Write-Host ""
Write-Host "==> Starting Celery worker (lumen2, concurrency=$concurrency)..."
Write-Host "    Broker: $env:CELERY_BROKER_URL"
Write-Host "    Qdrant: $($env:QDRANT_HOST):$($env:QDRANT_PORT)  collection=$env:QDRANT_COLLECTION_NAME"
Write-Host "    Device: $env:EMBEDDING_DEVICE"
Write-Host "    Worker: $hostname"
Write-Host ""

Set-Location $WORKER_DIR

& "$VENV_DIR\Scripts\celery.exe" -A celery_app worker `
    --loglevel=info `
    --pool=solo `
    -E `
    --prefetch-multiplier=1 `
    --queues=celery `
    --hostname=$hostname
