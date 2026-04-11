"""
Recovery router — POST /api/admin/recovery/scan

Triggers the worker recovery agent to detect and remediate stuck/errored
media files. Auth is applied at include_router level in main.py.
"""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.ops.recovery_agent import recovery_agent

router = APIRouter()


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
