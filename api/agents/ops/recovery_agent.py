"""
Worker Recovery Agent — LangGraph state machine for automated pipeline error recovery.

State machine topology:
    scan_errors → investigate → execute_recovery → audit → END

Triggered via POST /api/admin/recovery/scan.
Set dry_run=True to preview without making changes.

Error classes handled:
  - EIO (SMB disconnect): flagged, not auto-recovered — operator must remount
  - stuck (processing > 2h): reset to 'pending', re-dispatched
  - retryable (non-EIO errors): reset to 'pending', re-dispatched
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from typing_extensions import NotRequired, TypedDict

from celery import Celery
from langgraph.graph import END, StateGraph
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB + Celery clients (lazy singletons, same pattern as stats.py)
# ---------------------------------------------------------------------------

_engine = None


def _get_session():
    global _engine
    if _engine is None:
        _engine = create_engine(
            os.getenv(
                "DATABASE_URL",
                "postgresql://lumen_user:secure_password_here@postgres:5432/lumen",
            ),
            echo=False,
            pool_pre_ping=True,
        )
    return sessionmaker(bind=_engine)()


_celery_app = Celery(
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class RecoveryState(TypedDict):
    scan_id: str
    dry_run: bool

    # Populated by scan_errors
    eio_files: NotRequired[list[dict]]
    stuck_files: NotRequired[list[dict]]
    retryable_files: NotRequired[list[dict]]
    error_summary: NotRequired[str]

    # Populated by investigate
    investigation: NotRequired[str]
    recovery_plan: NotRequired[list[dict]]

    # Populated by execute_recovery
    recovered_count: NotRequired[int]
    skipped_count: NotRequired[int]
    failed_count: NotRequired[int]
    task_ids: NotRequired[list[str]]
    execution_errors: NotRequired[list[str]]

    # Populated by audit
    summary: NotRequired[str]


# ---------------------------------------------------------------------------
# Node: scan_errors
# ---------------------------------------------------------------------------


async def scan_errors(state: RecoveryState) -> dict:  # pragma: no cover
    """Query DB for errored and stuck files, grouped by error class."""
    db = _get_session()
    try:
        eio_rows = db.execute(
            text("""
                SELECT id::text, file_path, file_type, error_message, created_at
                FROM media_files
                WHERE processing_status = 'error'
                  AND error_message LIKE '%EIO%'
                ORDER BY created_at DESC
                LIMIT 100
            """)
        ).fetchall()
        eio_files = [
            {
                "id": r[0],
                "file_path": r[1],
                "file_type": r[2],
                "error_message": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in eio_rows
        ]

        stuck_rows = db.execute(
            text("""
                SELECT id::text, file_path, file_type, created_at
                FROM media_files
                WHERE processing_status = 'processing'
                  AND processed_at IS NULL
                  AND created_at < NOW() - INTERVAL '2 hours'
                ORDER BY created_at ASC
                LIMIT 100
            """)
        ).fetchall()
        stuck_files = [
            {
                "id": r[0],
                "file_path": r[1],
                "file_type": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
            }
            for r in stuck_rows
        ]

        retry_rows = db.execute(
            text("""
                SELECT id::text, file_path, file_type, error_message, created_at
                FROM media_files
                WHERE processing_status = 'error'
                  AND (error_message NOT LIKE '%EIO%' OR error_message IS NULL)
                ORDER BY created_at DESC
                LIMIT 100
            """)
        ).fetchall()
        retryable_files = [
            {
                "id": r[0],
                "file_path": r[1],
                "file_type": r[2],
                "error_message": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in retry_rows
        ]

        error_groups = db.execute(
            text("""
                SELECT LEFT(COALESCE(error_message, '(null)'), 120) AS snippet,
                       COUNT(*) AS cnt
                FROM media_files
                WHERE processing_status = 'error'
                GROUP BY LEFT(COALESCE(error_message, '(null)'), 120)
                ORDER BY cnt DESC
                LIMIT 10
            """)
        ).fetchall()
        error_summary = (
            "\n".join(f"  [{row[1]} files] {row[0]}" for row in error_groups)
            or "  (no errors found)"
        )

        return {
            "eio_files": eio_files,
            "stuck_files": stuck_files,
            "retryable_files": retryable_files,
            "error_summary": error_summary,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Node: investigate
# ---------------------------------------------------------------------------


async def investigate(state: RecoveryState) -> dict:  # pragma: no cover
    """Use the configured LLM provider to classify errors and recommend actions."""
    from dependencies import get_llm_provider

    llm = get_llm_provider()
    eio_count = len(state.get("eio_files", []))
    stuck_count = len(state.get("stuck_files", []))
    retry_count = len(state.get("retryable_files", []))

    prompt = f"""You are a media pipeline operations agent. Analyze these pipeline errors and decide recovery actions.

PIPELINE CONTEXT:
- Media files flow: pending → processing → done | error
- EIO = SMB mount disconnected — cannot auto-fix, operator must remount + reset
- stuck = status 'processing' for >2h with no processed_at — worker died, safe to reset
- retryable = non-EIO errors — safe to reset to pending and re-dispatch

CURRENT STATE:
EIO errors: {eio_count} files
Stuck (>2h): {stuck_count} files
Retryable errors: {retry_count} files

TOP ERROR GROUPS:
{state.get("error_summary", "(none)")}

Reply with ONLY a JSON object — no markdown, no explanation:
{{
  "investigation": "<2-3 sentence assessment of pipeline health>",
  "recovery_plan": [
    {{
      "group": "stuck" | "retryable" | "eio",
      "action": "reset_pending" | "operator_required",
      "count": <number>,
      "rationale": "<one sentence>"
    }}
  ]
}}
Only include groups with count > 0. EIO must always be operator_required."""

    try:
        import asyncio as _asyncio
        raw = await _asyncio.wait_for(
            llm.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=2048,
            ),
            timeout=float(os.getenv("RECOVERY_LLM_TIMEOUT", "10")),
        )
        content = raw.strip()
        # Strip thinking tokens (gemma4, qwen3 extended-thinking models)
        import re as _re
        content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL).strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.split("```")[0].strip()
        parsed = json.loads(content)
        return {
            "investigation": parsed.get("investigation", ""),
            "recovery_plan": parsed.get("recovery_plan", []),
        }
    except Exception as exc:
        log.warning("LLM investigation failed (%s) — using rule-based fallback", exc)
        plan = []
        if stuck_count:
            plan.append(
                {
                    "group": "stuck",
                    "action": "reset_pending",
                    "count": stuck_count,
                    "rationale": "Stuck tasks — worker likely died, safe to reset",
                }
            )
        if retry_count:
            plan.append(
                {
                    "group": "retryable",
                    "action": "reset_pending",
                    "count": retry_count,
                    "rationale": "Non-EIO errors — reset to pending and re-dispatch",
                }
            )
        if eio_count:
            plan.append(
                {
                    "group": "eio",
                    "action": "operator_required",
                    "count": eio_count,
                    "rationale": "SMB mount error — operator must remount and reset manually",
                }
            )
        return {
            "investigation": (
                f"LLM unavailable — rule-based fallback: "
                f"{stuck_count} stuck, {retry_count} retryable, {eio_count} EIO."
            ),
            "recovery_plan": plan,
        }


# ---------------------------------------------------------------------------
# Node: execute_recovery
# ---------------------------------------------------------------------------


async def execute_recovery(state: RecoveryState) -> dict:  # pragma: no cover
    """Reset fixable files to 'pending' and re-dispatch their Celery tasks."""
    dry_run = state.get("dry_run", False)
    plan = state.get("recovery_plan", [])
    file_pool = {
        "stuck": state.get("stuck_files", []),
        "retryable": state.get("retryable_files", []),
        "eio": state.get("eio_files", []),
    }

    recovered_count = 0
    skipped_count = 0
    failed_count = 0
    task_ids: list[str] = []
    execution_errors: list[str] = []

    for step in plan:
        group = step["group"]
        action = step["action"]
        files = file_pool.get(group, [])

        if action != "reset_pending" or not files:
            skipped_count += len(files)
            continue

        file_ids = [f["id"] for f in files]

        if not dry_run:
            db = _get_session()
            try:
                db.execute(
                    text("""
                        UPDATE media_files
                        SET processing_status    = 'pending',
                            error_message        = NULL,
                            embedding_started_at = NULL
                        WHERE id = ANY(CAST(:ids AS uuid[]))
                          AND processing_status IN ('error', 'processing')
                    """),
                    {"ids": file_ids},
                )
                db.commit()
            except Exception as exc:
                log.error("DB reset failed for group '%s': %s", group, exc)
                db.rollback()
                execution_errors.append(f"DB reset failed ({group}): {exc}")
                failed_count += len(files)
                db.close()
                continue
            finally:
                db.close()

        recovered_count += len(files)

        if not dry_run:
            for f in files:
                try:
                    task_name = (
                        "tasks.process_video"
                        if f["file_type"] == "video"
                        else "tasks.process_image"
                    )
                    result = _celery_app.send_task(
                        task_name,
                        args=[f["file_path"], f["id"]],
                        queue="celery",
                    )
                    task_ids.append(result.id)
                except Exception as exc:
                    log.warning("Dispatch failed for %s: %s", f["file_path"], exc)
                    execution_errors.append(f"Dispatch failed ({f['file_path']}): {exc}")
                    failed_count += 1

    return {
        "recovered_count": recovered_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "task_ids": task_ids,
        "execution_errors": execution_errors,
    }


# ---------------------------------------------------------------------------
# Node: audit
# ---------------------------------------------------------------------------


async def audit(state: RecoveryState) -> dict:  # pragma: no cover
    """Persist a one-line recovery summary to audit_logs."""
    parts = [
        f"scan_id={state['scan_id']}",
        f"dry_run={state.get('dry_run', False)}",
        f"recovered={state.get('recovered_count', 0)}",
        f"skipped={state.get('skipped_count', 0)}",
        f"failed={state.get('failed_count', 0)}",
        f"dispatched={len(state.get('task_ids', []))}",
    ]
    summary = " | ".join(parts)

    if not state.get("dry_run", False):
        try:
            db = _get_session()
            try:
                db.execute(
                    text("""
                        INSERT INTO audit_logs
                            (id, timestamp, endpoint, method, response_status, response_ms, details)
                        VALUES
                            (:id, :ts, :endpoint, 'POST', 200, 0, :details)
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "ts": datetime.now(timezone.utc),
                        "endpoint": f"/api/admin/recovery/scan:{state['scan_id']}",
                        "details": summary,
                    },
                )
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            log.warning("Audit log write failed: %s", exc)

    return {"summary": summary}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_recovery_agent():
    graph = StateGraph(RecoveryState)

    graph.add_node("scan_errors", scan_errors)
    graph.add_node("investigate", investigate)
    graph.add_node("execute_recovery", execute_recovery)
    graph.add_node("audit", audit)

    graph.set_entry_point("scan_errors")
    graph.add_edge("scan_errors", "investigate")
    graph.add_edge("investigate", "execute_recovery")
    graph.add_edge("execute_recovery", "audit")
    graph.add_edge("audit", END)

    return graph.compile()


recovery_agent = build_recovery_agent()
