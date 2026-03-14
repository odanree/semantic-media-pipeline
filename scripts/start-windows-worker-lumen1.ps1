# ============================================================================
# start-windows-worker-lumen1.ps1
# Start the Windows-native Celery worker for lumen1 with CUDA GPU.
# ============================================================================

$ErrorActionPreference = "Stop"

$VENV_DIR  = "C:\lumen-worker-venv"
$PROJ_DIR  = (Resolve-Path "$PSScriptRoot\..").Path
$ENV_FILE  = "$PROJ_DIR\.env.windows-worker-lumen1"
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
# 2. Test CUDA
# ----------------------------------------------------------------------------
Write-Host "==> Testing CUDA..."
$cudaTest = & "$VENV_DIR\Scripts\python.exe" -c @"
import torch
print(f'   CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'   GPU: {torch.cuda.get_device_name(0)}')
"@
Write-Host $cudaTest

# ----------------------------------------------------------------------------
# 3. Start Celery worker
# ----------------------------------------------------------------------------
$concurrency = if ($env:CELERY_CONCURRENCY) { $env:CELERY_CONCURRENCY } else { "1" }
$instance    = Get-Random -Minimum 1 -Maximum 9999
$hostname    = "win-lumen1-$instance@$env:COMPUTERNAME"

Write-Host ""
Write-Host "==> Starting Celery worker (lumen1, concurrency=$concurrency)..."
Write-Host "    Broker: $env:CELERY_BROKER_URL"
Write-Host "    Qdrant: $($env:QDRANT_HOST):$($env:QDRANT_PORT)  collection=$env:QDRANT_COLLECTION_NAME"
Write-Host "    Device: $env:EMBEDDING_DEVICE"
Write-Host "    Worker: $hostname"
Write-Host ""

Set-Location $WORKER_DIR

& "$VENV_DIR\Scripts\celery.exe" -A celery_app worker `
    --loglevel=info `
    --pool=prefork `
    -E `
    --concurrency=$concurrency `
    --prefetch-multiplier=1 `
    --max-tasks-per-child=$(if ($env:CELERY_MAX_TASKS_PER_CHILD) { $env:CELERY_MAX_TASKS_PER_CHILD } else { "50" }) `
    --queues=celery `
    --hostname=$hostname
