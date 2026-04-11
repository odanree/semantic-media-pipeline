"""
Recovery router

POST /api/admin/recovery/scan  — run the recovery agent
GET  /api/admin/recovery/history — audit log of past recovery runs

Auth is applied at include_router level in main.py.
"""

import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from agents.ops.recovery_agent import recovery_agent

router = APIRouter()

# ---------------------------------------------------------------------------
# Sync DB session (same pattern as stats.py)
# ---------------------------------------------------------------------------

_engine = None


def _get_session():
    global _engine
    if _engine is None:
        _engine = create_engine(
            os.getenv(
                "DATABASE_URL",
                "postgresql://lumen_user:secure_password_here@lumen-postgres:5432/lumen",
            ),
            echo=False,
            pool_pre_ping=True,
        )
    return sessionmaker(bind=_engine)()


class RecoveryHistoryEntry(BaseModel):
    id: str
    timestamp: str
    scan_id: str
    dry_run: bool
    recovered: int
    skipped: int
    failed: int
    dispatched: int


class RecoveryScanRequest(BaseModel):
    dry_run: bool = False


class RecoveryScanResponse(BaseModel):
    scan_id: str
    dry_run: bool
    eio_count: int
    stuck_count: int
    retryable_count: int
    recovered_count: int       # files reset (or would-reset in dry_run)
    skipped_count: int         # files not acted on (e.g. operator_required)
    failed_count: int
    tasks_dispatched: int      # always 0 in dry_run
    investigation: str
    recovery_plan: list[dict]
    summary: str
    execution_errors: list[str]


def _parse_details(details: str) -> dict:
    """Parse 'key=val | key=val' summary string into a dict."""
    out: dict = {}
    for part in details.split(" | "):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


@router.get("/admin/recovery/history", response_model=list[RecoveryHistoryEntry], tags=["admin"])
def get_recovery_history(limit: int = 50):
    """
    Return the last N live recovery runs from audit_logs, newest first.
    Dry runs are excluded — only actual recoveries are recorded with details.
    """
    db = _get_session()
    try:
        rows = db.execute(
            text("""
                SELECT id::text, timestamp, endpoint, details
                FROM audit_logs
                WHERE endpoint LIKE '/api/admin/recovery/scan:%'
                  AND details IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()
    finally:
        db.close()

    entries = []
    for row in rows:
        row_id, ts, endpoint, details = row
        scan_id = endpoint.split(":")[-1] if ":" in endpoint else ""
        parsed = _parse_details(details)
        entries.append(RecoveryHistoryEntry(
            id=row_id,
            timestamp=ts.isoformat(),
            scan_id=scan_id,
            dry_run=parsed.get("dry_run", "False").lower() == "true",
            recovered=int(parsed.get("recovered", 0)),
            skipped=int(parsed.get("skipped", 0)),
            failed=int(parsed.get("failed", 0)),
            dispatched=int(parsed.get("dispatched", 0)),
        ))
    return entries


@router.post("/admin/recovery/scan", response_model=RecoveryScanResponse, tags=["admin"])
async def run_recovery_scan(body: RecoveryScanRequest = RecoveryScanRequest()):
    """
    Run the recovery agent against the current pipeline state.

    - **EIO errors** (SMB disconnects): reported but not auto-recovered —
      operator must remount the share and manually reset status.
    - **Stuck files** (processing > 2h): reset to `pending` and re-dispatched.
    - **Retryable errors** (non-EIO): reset to `pending` and re-dispatched.

    Use `dry_run=true` to preview what would be recovered without any changes.
    """
    scan_id = str(uuid.uuid4())[:8]
    initial_state = {"scan_id": scan_id, "dry_run": body.dry_run}

    try:
        final_state = await recovery_agent.ainvoke(initial_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recovery agent failed: {exc}")

    return RecoveryScanResponse(
        scan_id=scan_id,
        dry_run=body.dry_run,
        eio_count=len(final_state.get("eio_files", [])),
        stuck_count=len(final_state.get("stuck_files", [])),
        retryable_count=len(final_state.get("retryable_files", [])),
        recovered_count=final_state.get("recovered_count", 0),
        skipped_count=final_state.get("skipped_count", 0),
        failed_count=final_state.get("failed_count", 0),
        tasks_dispatched=len(final_state.get("task_ids", [])),
        investigation=final_state.get("investigation", ""),
        recovery_plan=final_state.get("recovery_plan", []),
        summary=final_state.get("summary", ""),
        execution_errors=final_state.get("execution_errors", []),
    )
