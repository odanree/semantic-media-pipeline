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
# 2. Test DirectML
# ----------------------------------------------------------------------------
Write-Host "==> Testing DirectML (RX 7900 XT)..."
$dmlTest = & "$VENV_DIR\Scripts\python.exe" -c @"
import torch_directml, torch
try:
    d = torch_directml.device(0)
    torch.zeros(1, device=d)
    print('   GPU OK: privateuseone:0 [DirectML ACTIVE]')
except Exception as e:
    print(f'   DirectML error: {e}')
    print('   Will fall back to CPU.')
"@
Write-Host $dmlTest

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
