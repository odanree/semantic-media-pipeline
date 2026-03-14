# ============================================================================
# setup-windows-worker.ps1
# One-time setup for the Windows-native Celery worker (lumen2, DirectML GPU).
# Run from PowerShell as a normal user.
# ============================================================================

$ErrorActionPreference = "Stop"

$VENV_DIR = "C:\lumen-worker-venv"

# ----------------------------------------------------------------------------
# 1. Find Python 3.10, 3.11, or 3.12
# ----------------------------------------------------------------------------
Write-Host "==> Checking for Python (3.10-3.12)..."
$PY_EXE = $null
$PY_VER = ""

# Try Python Launcher (official python.org installer)
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.11", "3.10")) {
        try {
            $ver = "$(& py "-$v" --version 2>&1)"
            if ($ver -like "Python 3.*") {
                $PY_EXE = "py"
                $PY_VER = "-$v"
                Write-Host "   Found via py launcher: $ver"
                break
            }
        } catch {}
    }
}

# Try Miniconda / Anaconda
if (-not $PY_EXE) {
    $candidates = @(
        "$env:USERPROFILE\miniconda3\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe",
        "C:\ProgramData\miniconda3\python.exe",
        "C:\ProgramData\anaconda3\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python311\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python310\python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            $ver = "$(& $p --version 2>&1)"
            if ($ver -like "Python 3.*") {
                $PY_EXE = $p
                Write-Host "   Found: $p  ($ver)"
                break
            }
        }
    }
}

# Try plain python on PATH
if (-not $PY_EXE) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $ver = "$(& python --version 2>&1)"
        if ($ver -like "Python 3.*") {
            $PY_EXE = "python"
            Write-Host "   Found on PATH: $ver"
        }
    }
}

if (-not $PY_EXE) {
    Write-Host ""
    Write-Host "ERROR: Python 3.10+ not found."
    Write-Host "Install via: winget install Python.Python.3.11"
    exit 1
}

# ----------------------------------------------------------------------------
# 2. Create virtual environment
# ----------------------------------------------------------------------------
Write-Host "==> Creating venv at $VENV_DIR..."
if ($PY_VER) {
    & $PY_EXE $PY_VER -m venv $VENV_DIR
} else {
    & $PY_EXE -m venv $VENV_DIR
}

$Pip = "$VENV_DIR\Scripts\pip.exe"

# ----------------------------------------------------------------------------
# 3. Install PyTorch (CPU base) + torch-directml
# ----------------------------------------------------------------------------
Write-Host "==> Installing PyTorch 2.4.1 (CPU wheels)..."
& $Pip install torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1 --index-url https://download.pytorch.org/whl/cpu

Write-Host "==> Installing torch-directml..."
& $Pip install torch-directml==0.2.5.dev240914

# ----------------------------------------------------------------------------
# 4. Install remaining worker dependencies
# ----------------------------------------------------------------------------
Write-Host "==> Installing worker dependencies..."
& $Pip install `
    "celery[redis]==5.6.2" `
    "redis==5.2.1" `
    "sentence-transformers==5.2.3" `
    "qdrant-client==1.17.0" `
    "SQLAlchemy==2.0.47" `
    "asyncpg==0.30.0" `
    "psycopg2-binary==2.9.10" `
    "Pillow==10.4.0" `
    "pillow-heif==0.14.0" `
    "opencv-python==4.13.0.92" `
    "tqdm==4.67.1" `
    "python-dotenv==1.0.1" `
    "pydantic==2.10.6" `
    "boto3==1.34.136" `
    "google-cloud-storage==2.16.0" `
    "ffmpeg-python==0.2.0" `
    "psutil==6.0.0" `
    "librosa>=0.10.2" `
    "soundfile>=0.12.1" `
    "scipy>=1.13.0" `
    "faster-whisper>=1.0.0" `
    "ultralytics>=8.0.0" `
    "prometheus-client==0.20.0"

# ----------------------------------------------------------------------------
# 5. Check / install FFmpeg
# ----------------------------------------------------------------------------
Write-Host "==> Checking FFmpeg..."
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    $ffver = "$(ffmpeg -version 2>&1 | Select-Object -First 1)"
    Write-Host "   FFmpeg already in PATH: $ffver"
} else {
    Write-Host "   Installing FFmpeg via winget..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Write-Host "   NOTE: Open a new PowerShell window so FFmpeg appears on PATH."
}

# ----------------------------------------------------------------------------
# 6. Create output directories on I: (Sabrent V2)
# ----------------------------------------------------------------------------
Write-Host "==> Creating output directories on I:..."
foreach ($dir in @("I:\lumen2-frame-cache", "I:\lumen2-media", "I:\lumen2-proxies")) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-Host "   Created $dir"
    } else {
        Write-Host "   Exists: $dir"
    }
}

# ----------------------------------------------------------------------------
# Done
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Setup complete!"
Write-Host "    Next: .\scripts\start-windows-worker-lumen2.ps1"
