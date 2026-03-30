# Claude Code Guidelines for Semantic Media Pipeline

> Last updated: 2026-03-30 (git history cleaned, sensitive data removed)

## Critical Git Workflow Rules

🚨 **NEVER push directly to main/master.** Always use feature branches and create PRs.

- **Always create a feature branch** for any changes: `git checkout -b feat/<description>` or `fix/<description>`
- **Never force push** to any branch
- **Always create a PR** via `gh pr create --fill` before merging
- **Confirm branch before commits**: Run `git branch --show-current` to verify you're not on main
- **Confirm repo before any git operations**: Run `git remote -v` to ensure you're in the correct repository

### PR Workflow
1. Verify current branch with `git branch --show-current` (should NOT be main)
2. Make code changes
3. Run linters and tests locally before committing
4. Commit with clear messages (no AI attribution unless explicitly requested)
5. Push branch: `git push origin <branch-name>`
6. Create PR: `gh pr create --fill` (do NOT merge yourself)

## CI/CD Debugging Process

When fixing CI/CD failures:

1. **Read the actual failing check logs first** — don't assume what's wrong
2. **Focus ONLY on the checks that are actually failing** — ignore coverage, lint, or type issues unless they're in the failing checks
3. **Run local verification before pushing**:
   - For frontend: `npm run lint && npm run typecheck && npm test`
   - For backend: `ruff check . --select=E,F,W --ignore=E501,E402,F401`
4. **Create a feature branch** (never push to main to "test" CI)
5. **Show the final passing check output** before asking permission to push

### Common Pitfalls
- Don't spend time on coverage thresholds or secondary lint issues unless those are the actual failing checks
- Don't assume the error — read the log
- Don't refactor unrelated code while fixing CI errors

## Project Context

**Primary Languages:**
- TypeScript (frontend, Next.js)
- Python (backend, FastAPI)
- Markdown (docs)
- YAML (Docker, CI configs)

**Infrastructure:**
- Deployment: Hetzner VPS
- Container orchestration: Docker Compose
- Monitoring: Prometheus/Grafana (production)
- Database: Qdrant vector DB, PostgreSQL
- Frontend: Next.js 15, React 18
- Backend: FastAPI, Celery (background tasks)

**Chat Session Preservation:**
Always open the project via the workspace file to preserve chat histories:
```bash
code c:/Users/Danh/Documents/Projects/workspace.code-workspace
```
If you move the project folder without using the workspace file, chat sessions will be lost. See `c:\Users\Danh\Documents\Projects\job-search-pipeline\.claude\README.md` for recovery instructions.

## Repository Structure

- **Private repo (this one)**: Development and experimentation
- **Public repo** (`semantic-media-pipeline`): Published features (develop here first, sync after PR merges)
- **Worker**: Docker Compose services — use `docker-compose up -d --build` (not restart) after code changes to ensure containers rebuild

## Development Workflow

### For Feature Branches
1. Start in private repo, create feature branch
2. Implement and test thoroughly
3. Create PR and ensure all CI checks pass
4. Get approval if needed
5. Merge to main in private repo
6. Later, PR changes back to public repo if needed

### For Deployment Changes
1. Test in private repo first
2. Never skip pre-commit hooks or safety checks
3. Always verify containers rebuilt after code changes: `docker ps --format '{{.Image}} {{.CreatedAt}}'`
4. Confirm new code is actually running before marking deploy complete

## Common Commands

```bash
# Verify repo and branch (do this at session start)
git remote -v
git branch --show-current

# Create a feature branch
git checkout -b feat/my-feature

# Before committing
npm run lint && npm run typecheck && npm test  # Frontend
ruff check . --select=E,F,W --ignore=E501,E402,F401  # Backend

# Push and create PR
git push origin feat/my-feature
gh pr create --fill

# Check PR status
gh pr view <number> --json statusCheckRollup

# Run tests locally with memory management
cd frontend && npm test  # Uses optimized vitest config
```

## Known Issues & Workarounds

- **Frontend tests memory**: Tests run serially (maxWorkers: 1) to prevent heap out of memory. This is slower but necessary.
- **Docker cache stale code**: Always use `--build` flag when restarting services after code changes
- **DNS/SSL issues**: Cloudflare proxy can cause problems — verify with `curl -I https://[domain]`

## Local Development (lumen2)

When running scripts locally against lumen2 Docker containers (e.g., `scripts/clear_votes.py`):

1. Create `.env.lumen2.local` to override Docker hostnames with localhost (see `.env` for actual password):
```bash
QDRANT_HOST=localhost
QDRANT_PORT=6340
QDRANT_COLLECTION_NAME=media_vectors2
DATABASE_HOST=localhost
DATABASE_PORT=5433
DATABASE_USER=lumen2_user
DATABASE_NAME=lumen2
DATABASE_PASSWORD=${DATABASE_PASSWORD_2}  # Get from .env
DATABASE_ASYNC_URL=postgresql+asyncpg://lumen2_user:${DATABASE_PASSWORD_2}@localhost:5433/lumen2
```

2. Scripts that load `.env` will automatically detect `--project=lumen2` and load `.env.lumen2.local` overrides.

**Key differences for lumen2:**
- lumen2-qdrant: `127.0.0.1:6340->6333` (Qdrant port 6333 → host 6340)
- lumen2-postgres: `127.0.0.1:5433->5432` (PostgreSQL port 5432 → host 5433)
- Collection: `media_vectors2` (lumen2-specific, not `media_vectors`)
- User: `lumen2_user` (not `lumen_user`)
- Database: `lumen2` (not `lumen`)

**.env.lumen2.local is gitignored** — follows `.env.*.local` pattern in .gitignore. Never commit with actual passwords.

## Questions?

If anything is unclear or you encounter a situation that conflicts with these guidelines, ask first before proceeding.
