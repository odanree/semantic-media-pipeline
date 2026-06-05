"""
Held-out Evaluation Set Router

A small CRUD API for the manually-curated eval set. This set is intentionally
decoupled from `vote_events` so that the same labels can't drive both training
and evaluation — that would silently inflate held-out accuracy.

Endpoints:
  POST   /api/eval-set         — add (upsert) one entry
  GET    /api/eval-set         — list entries; optional ?query=... filter
  DELETE /api/eval-set/{id}    — remove one entry
"""

import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

router = APIRouter()

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


class EvalEntryIn(BaseModel):
    search_query: str = Field(..., min_length=1, max_length=512)
    file_path: str = Field(..., min_length=1)
    audio_segment_index: Optional[int] = None
    label: int = Field(..., description="1 (positive) or -1 (negative)")
    qdrant_point_id: Optional[str] = None
    note: Optional[str] = None


class EvalEntryOut(BaseModel):
    id: str
    search_query: str
    file_path: str
    audio_segment_index: Optional[int]
    label: int
    qdrant_point_id: Optional[str]
    note: Optional[str]
    created_at: str


@router.post("/eval-set", status_code=201)
def add_eval_entry(body: EvalEntryIn):
    if body.label not in (1, -1):
        raise HTTPException(status_code=400, detail="label must be 1 or -1")

    db = _get_session()
    try:
        # Upsert on (search_query, file_path, COALESCE(audio_segment_index, -1)).
        # ON CONFLICT updates label/note/qdrant_point_id, preserving id + created_at.
        row = db.execute(text("""
            INSERT INTO eval_set
                (search_query, file_path, audio_segment_index, label, qdrant_point_id, note)
            VALUES
                (:q, :fp, :seg, :label, CAST(:pid AS UUID), :note)
            ON CONFLICT (search_query, file_path, COALESCE(audio_segment_index, -1))
            DO UPDATE SET
                label = EXCLUDED.label,
                qdrant_point_id = EXCLUDED.qdrant_point_id,
                note = EXCLUDED.note
            RETURNING id, search_query, file_path, audio_segment_index,
                      label, qdrant_point_id, note, created_at
        """), {
            "q":     body.search_query,
            "fp":    body.file_path,
            "seg":   body.audio_segment_index,
            "label": body.label,
            "pid":   body.qdrant_point_id,
            "note":  body.note,
        }).fetchone()
        db.commit()
        return EvalEntryOut(
            id=str(row[0]),
            search_query=row[1],
            file_path=row[2],
            audio_segment_index=row[3],
            label=row[4],
            qdrant_point_id=str(row[5]) if row[5] else None,
            note=row[6],
            created_at=row[7].isoformat(),
        )
    finally:
        db.close()


@router.get("/eval-set")
def list_eval_entries(
    query: Optional[str] = Query(None, description="Filter by exact search_query"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    db = _get_session()
    try:
        sql = """
            SELECT id, search_query, file_path, audio_segment_index,
                   label, qdrant_point_id, note, created_at
            FROM eval_set
            WHERE (:q IS NULL OR search_query = :q)
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """
        rows = db.execute(text(sql), {"q": query, "limit": limit, "offset": offset}).fetchall()
        total = db.execute(
            text("SELECT COUNT(*) FROM eval_set WHERE (:q IS NULL OR search_query = :q)"),
            {"q": query},
        ).scalar()
        return {
            "total": int(total or 0),
            "entries": [
                {
                    "id":                  str(r[0]),
                    "search_query":        r[1],
                    "file_path":           r[2],
                    "audio_segment_index": r[3],
                    "label":               r[4],
                    "qdrant_point_id":     str(r[5]) if r[5] else None,
                    "note":                r[6],
                    "created_at":          r[7].isoformat(),
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.delete("/eval-set/{entry_id}", status_code=204)
def delete_eval_entry(entry_id: str):
    try:
        uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="entry_id must be a UUID")

    db = _get_session()
    try:
        result = db.execute(
            text("DELETE FROM eval_set WHERE id = CAST(:eid AS UUID)"),
            {"eid": entry_id},
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="entry not found")
        return None
    finally:
        db.close()
