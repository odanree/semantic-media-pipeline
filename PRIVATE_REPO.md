# Private Repository

This is the private fork of [semantic-media-pipeline](https://github.com/odanree/semantic-media-pipeline).

## Purpose

- **Production deployment target.** All pushes to `main` here trigger the deploy workflow to the production server.
- **Private features.** Features that should not be open-sourced (e.g. similarity scoring, internal tooling) live here and are never upstreamed.

## Workflow

```
Public repo (open-source features)
        │
        │  git fetch public main
        │  git merge public/main
        ▼
Private repo ──► deploy to prod
```

Public features are periodically merged into this repo and deployed to production from here. The public repo is never deployed to prod directly.

## Syncing from the public repo

```bash
# From this repo's working directory
git fetch public main
git merge public/main
git push origin main
```

If `public` remote is not set up locally:

```bash
git remote add public https://github.com/odanree/semantic-media-pipeline.git
```
