#!/usr/bin/env powershell
<#
.SYNOPSIS
Regenerates vendor wheels for API and Worker services.

.DESCRIPTION
Use this script whenever you update requirements.txt files to regenerate
pre-built wheels for faster Docker builds.

.EXAMPLE
./regenerate-wheels.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Blue
Write-Host "Regenerating Vendor Wheels" -ForegroundColor Blue
Write-Host "================================" -ForegroundColor Blue

# Check pip is available
if (-not (Get-Command pip -ErrorAction SilentlyContinue)) {
    Write-Error "pip not found. Please install Python and activate your virtual environment."
    exit 1
}

$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# API Wheels
Write-Host "`n[1/2] Building API wheels..." -ForegroundColor Cyan
$apiDir = Join-Path $rootDir "api"
$apiWheelsDir = Join-Path $apiDir "vendor\wheels"

# Clean old wheels
if (Test-Path $apiWheelsDir) {
    Remove-Item "$apiWheelsDir\*" -Recurse -Force
    Write-Host "  Cleaned old wheels" -ForegroundColor Gray
}

Push-Location $apiDir
pip wheel -r requirements.txt -w vendor\wheels --no-cache-dir | Select-String -Pattern "Successfully built|Skipped"
if ($LASTEXITCODE -ne 0) {
    Write-Error "API wheels generation failed"
    Pop-Location
    exit 1
}
Pop-Location

$apiWheelCount = (Get-ChildItem "$apiWheelsDir\*.whl" -ErrorAction SilentlyContinue).Count
Write-Host "  ✓ Generated $apiWheelCount wheels" -ForegroundColor Green

# Worker Wheels
Write-Host "`n[2/2] Building Worker wheels..." -ForegroundColor Cyan
$workerDir = Join-Path $rootDir "worker"
$workerWheelsDir = Join-Path $workerDir "vendor\wheels"

# Clean old wheels
if (Test-Path $workerWheelsDir) {
    Remove-Item "$workerWheelsDir\*" -Recurse -Force
    Write-Host "  Cleaned old wheels" -ForegroundColor Gray
}

Push-Location $workerDir
pip wheel -r requirements.txt -w vendor\wheels --no-cache-dir | Select-String -Pattern "Successfully built|Skipped"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Worker wheels generation failed"
    Pop-Location
    exit 1
}
Pop-Location

$workerWheelCount = (Get-ChildItem "$workerWheelsDir\*.whl" -ErrorAction SilentlyContinue).Count
Write-Host "  ✓ Generated $workerWheelCount wheels" -ForegroundColor Green

# Summary
Write-Host "`n================================" -ForegroundColor Blue
Write-Host "✓ Wheels regenerated successfully!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Blue
Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "  1. Run: docker-compose up --build"
Write-Host "  2. Subsequent rebuilds will be 30sec instead of 7+ minutes"
Write-Host "  3. Commit vendor/wheels/ to git (or add to .gitignore for CI regeneration)"
