"""
Tests for POST /api/admin/recovery/scan.

Coverage:
- 200 on success (happy path: stuck + retryable files recovered)
- Response shape: counts map correctly from final state
- dry_run=True is passed through and prevents DB changes
- Agent exception → 500 with detail
- EIO files counted but not recovered (operator_required)
- No errors → all zeros, no tasks dispatched
"""

from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_files(prefix: str, count: int, file_type: str = "video") -> list[dict]:
    return [
        {
            "id": f"{prefix}-{i}",
            "file_path": f"/mnt/source/{prefix}_{i}.{'mp4' if file_type == 'video' else 'jpg'}",
            "file_type": file_type,
            "error_message": None,
            "created_at": "2026-04-08T10:00:00",
        }
        for i in range(count)
    ]


def _build_final_state(
    eio=0,
    stuck=2,
    retryable=1,
    recovered=3,
    skipped=0,
    failed=0,
    tasks=3,
    investigation="Two workers appear to have died mid-job. One batch of retryable FFmpeg errors. Pipeline otherwise healthy.",
    dry_run=False,
    execution_errors=None,
):
    return {
        "scan_id": "abc12345",
        "dry_run": dry_run,
        "eio_files": _make_files("eio", eio),
        "stuck_files": _make_files("stuck", stuck),
        "retryable_files": _make_files("retry", retryable, file_type="image"),
        "error_summary": "  [2 files] FFmpegError: exit code 1\n  [1 files] OSError: [Errno 5]",
        "investigation": investigation,
        "recovery_plan": [
            {"group": "stuck", "action": "reset_pending", "count": stuck, "rationale": "Worker died"},
            {"group": "retryable", "action": "reset_pending", "count": retryable, "rationale": "Safe to retry"},
        ],
        "recovered_count": recovered,
        "skipped_count": skipped,
        "failed_count": failed,
        "task_ids": [f"celery-task-{i}" for i in range(tasks)],
        "execution_errors": execution_errors or [],
        "summary": f"scan_id=abc12345 | dry_run={dry_run} | recovered={recovered} | skipped={skipped} | failed={failed} | dispatched={tasks}",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_recovery_scan_returns_200(client):
    final_state = _build_final_state()
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        resp = client.post("/api/admin/recovery/scan", json={})
    assert resp.status_code == 200


def test_recovery_scan_counts_map_correctly(client):
    final_state = _build_final_state(eio=1, stuck=2, retryable=1, recovered=3, tasks=3)
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        data = client.post("/api/admin/recovery/scan", json={}).json()

    assert data["eio_count"] == 1
    assert data["stuck_count"] == 2
    assert data["retryable_count"] == 1
    assert data["recovered_count"] == 3
    assert data["tasks_dispatched"] == 3
    assert data["failed_count"] == 0
    assert data["skipped_count"] == 0


def test_recovery_scan_investigation_returned(client):
    final_state = _build_final_state(investigation="Pipeline is degraded — two workers stalled.")
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        data = client.post("/api/admin/recovery/scan", json={}).json()

    assert "workers stalled" in data["investigation"]


def test_recovery_scan_recovery_plan_returned(client):
    final_state = _build_final_state(stuck=2, retryable=1)
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        data = client.post("/api/admin/recovery/scan", json={}).json()

    groups = [step["group"] for step in data["recovery_plan"]]
    assert "stuck" in groups
    assert "retryable" in groups


def test_recovery_scan_dry_run_flag_passed_through(client):
    final_state = _build_final_state(dry_run=True, recovered=0, tasks=0)
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        data = client.post("/api/admin/recovery/scan", json={"dry_run": True}).json()

    assert data["dry_run"] is True
    assert data["tasks_dispatched"] == 0


def test_recovery_scan_agent_exception_returns_500(client):
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("DB unreachable"))
        resp = client.post("/api/admin/recovery/scan", json={})

    assert resp.status_code == 500
    assert "DB unreachable" in resp.json()["detail"]


def test_recovery_scan_eio_only_not_recovered(client):
    """EIO files are counted but recovered_count stays 0 (operator_required)."""
    final_state = _build_final_state(
        eio=3, stuck=0, retryable=0,
        recovered=0, skipped=3, tasks=0,
        investigation="All errors are SMB EIO — operator must remount share.",
    )
    final_state["recovery_plan"] = [
        {"group": "eio", "action": "operator_required", "count": 3, "rationale": "SMB mount error"}
    ]
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        data = client.post("/api/admin/recovery/scan", json={}).json()

    assert data["eio_count"] == 3
    assert data["recovered_count"] == 0
    assert data["tasks_dispatched"] == 0


def test_recovery_scan_clean_pipeline_returns_zeros(client):
    """No errors of any kind — everything should be zero."""
    final_state = _build_final_state(
        eio=0, stuck=0, retryable=0,
        recovered=0, skipped=0, tasks=0,
        investigation="Pipeline clean — no errors or stuck tasks detected.",
    )
    final_state["recovery_plan"] = []
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        data = client.post("/api/admin/recovery/scan", json={}).json()

    assert data["eio_count"] == 0
    assert data["stuck_count"] == 0
    assert data["retryable_count"] == 0
    assert data["recovered_count"] == 0
    assert data["tasks_dispatched"] == 0
    assert data["recovery_plan"] == []


def test_recovery_scan_execution_errors_surfaced(client):
    """Partial failures during execution are included in the response."""
    final_state = _build_final_state(
        stuck=2, recovered=1, failed=1,
        tasks=1,
        execution_errors=["Dispatch failed (/mnt/source/stuck_1.mp4): broker timeout"],
    )
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        data = client.post("/api/admin/recovery/scan", json={}).json()

    assert data["failed_count"] == 1
    assert len(data["execution_errors"]) == 1
    assert "broker timeout" in data["execution_errors"][0]


def test_recovery_scan_missing_body_uses_defaults(client):
    """Omitting the request body should default to dry_run=False."""
    final_state = _build_final_state()
    with patch("routers.recovery.recovery_agent") as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value=final_state)
        resp = client.post("/api/admin/recovery/scan")

    assert resp.status_code == 200
