# Test a single file from the worker container
# Usage: .\scripts\test-single-file.ps1 "path\to\file.mp4"

param(
    [Parameter(Mandatory=$true)]
    [string]$FilePath
)

# Convert Windows path to container path if needed
if ($FilePath -like "J:\*" -or $FilePath -like "J:/*") {
    # Windows J: drive -> container /mnt/source
    $ContainerPath = $FilePath.Replace("J:\", "/mnt/source/").Replace("J:/", "/mnt/source/").Replace("\", "/")
} else {
    $ContainerPath = $FilePath
}

Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "Test Single File Ingest" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "Windows Path: $FilePath"
Write-Host "Container Path: $ContainerPath"
Write-Host ""

# Run test script in worker container
docker exec lumen-worker python scripts/test_single_file.py $ContainerPath
