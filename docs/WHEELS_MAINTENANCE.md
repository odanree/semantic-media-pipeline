# Vendor Wheels Maintenance Guide

## Overview
Pre-built Python wheels are cached in `api/vendor/wheels` and `worker/vendor/wheels` for lightning-fast Docker rebuilds.

- **First build:** 5-10 minutes (generates wheels + builds Docker images)
- **Subsequent builds:** 30 seconds (uses pre-built wheels)

## Generated Spaces
```
api/vendor/wheels/           (~200 MB)
worker/vendor/wheels/         (~500 MB)
```

## When to Regenerate Wheels

**Regenerate wheels after:**
1. Updating `api/requirements.txt`
2. Updating `worker/requirements.txt`
3. Significant dependency changes (new packages)

**Do NOT regenerate if:**
- Only changing Python code (no requirements changes)
- Only rebuilding Docker images

## Regenerate Wheels Script

### Option 1: Manual Commands

```powershell
# For API
cd api
pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir

# For Worker
cd ../worker
pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir
```

### Option 2: One-Command Script

Create `regenerate-wheels.ps1`:

```powershell
#!/usr/bin/env powershell
Write-Host "Regenerating vendor wheels..." -ForegroundColor Green

# API wheels
Write-Host "Building API wheels..." -ForegroundColor Cyan
cd api
pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir
if ($LASTEXITCODE -ne 0) { Write-Error "API wheels failed"; exit 1 }

# Worker wheels
Write-Host "Building Worker wheels..." -ForegroundColor Cyan
cd ../worker
pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir
if ($LASTEXITCODE -ne 0) { Write-Error "Worker wheels failed"; exit 1 }

Write-Host "✓ Wheels regenerated successfully" -ForegroundColor Green
cd ..
```

Run it:
```powershell
./regenerate-wheels.ps1
```

## Git Strategy

### Option A: Commit wheels (simple, large repo)
```bash
git add api/vendor/wheels worker/vendor/wheels
git commit -m "Update vendor wheels"
```

**Pros:** No setup needed, reproducible builds
**Cons:** Large commits (~700 MB)

### Option B: .gitignore wheels (minimal repo, CI regenerates)
```gitignore
api/vendor/wheels/
worker/vendor/wheels/
```

Add to CI/CD to regenerate:
```yaml
# GitHub Actions example
- name: Regenerate wheels
  run: |
    cd api && pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir
    cd ../worker && pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir
```

**Pros:** Smaller repository
**Cons:** Need CI/CD setup

## Docker Build Performance

### Before (Old Dockerfile)
```
Step 1: FROM python:3.10-slim
Step 2: Install apt deps       (~2 min)
Step 3: pip install -r req     (~5 min)
Step 4: COPY source code       (~10 sec)
─────────────────────────────────
Total: ~7+ minutes
```

### After (Vendor Wheels)
```
Step 1: FROM python:3.10-slim
Step 2: Install apt deps       (~2 min - cached)
Step 3: COPY + pip install wheels (~20 sec)
Step 4: COPY source code       (~10 sec)
─────────────────────────────────
Total: ~30 seconds (on subsequent builds)
```

## Troubleshooting

### Issue: "No matching distribution found"
Wheels may have platform-specific builds. If encountering this:

```powershell
# Add --no-binary for that package
pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir --no-binary torch
```

### Issue: Wheels are stale
Regenerate with latest versions:

```powershell
pip install --upgrade -r requirements.txt
pip wheel -r requirements.txt -w vendor/wheels --no-cache-dir --upgrade
```

### Issue: Docker build still slow
Check if wheels are actually being used:
```bash
docker build --progress=plain api/ | grep "wheels"
```

If not copying, ensure `vendor/wheels/` exists and has `.whl` files:
```bash
ls api/vendor/wheels/
```

## Maintenance Checklist

- [ ] Update `requirements.txt` 
- [ ] Run wheel regeneration script
- [ ] Test `docker-compose up --build`
- [ ] Commit wheels (or CI regenerates them)
- [ ] Verify build time (~30 sec vs 7+ min)
