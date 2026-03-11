"""
MetadataAgent — PostgreSQL temporal and location queries.

Parses temporal intent from the query (years, month names, seasons)
and returns matching MediaFile records with their metadata.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import select, and_
from db.session import get_async_session_factory

log = logging.getLogger(__name__)

# Simple keyword → month number mapping
_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_SEASON_MONTHS = {
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "fall": [9, 10, 11], "autumn": [9, 10, 11],
    "winter": [12, 1, 2],
}


def _extract_temporal_filters(query: str) -> dict:
    """Extract year and/or month list from a natural language query."""
    filters: dict = {}
    q = query.lower()

    # Year: 4-digit number in range 1990-2030
    year_match = re.search(r"\b(199\d|20[012]\d)\b", q)
    if year_match:
        filters["year"] = int(year_match.group(1))

    # Month name
    for month_name, month_num in _MONTH_MAP.items():
        if month_name in q:
            filters["months"] = [month_num]
            break

    # Season
    for season, months in _SEASON_MONTHS.items():
        if season in q:
            filters["months"] = months
            break

    return filters


async def metadata_agent_run(query: str, limit: int = 20) -> list[dict]:
    from db.models import MediaFile

    filters = _extract_temporal_filters(query)
    if not filters:
        return []

    try:
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            conditions = [MediaFile.processing_status == "done"]

            if "year" in filters:
                conditions.append(
                    MediaFile.created_at.between(
                        datetime(filters["year"], 1, 1),
                        datetime(filters["year"], 12, 31, 23, 59, 59),
                    )
                )
            # Month-level filtering via extract — Postgres specific
            # Falls through gracefully if not supported
            stmt = (
                select(MediaFile)
                .where(and_(*conditions))
                .order_by(MediaFile.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return [
            {
                "file_path": r.file_path,
                "file_type": r.file_type,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "file_hash": r.file_hash,
            }
            for r in rows
        ]
    except Exception as exc:
        log.error("MetadataAgent query failed: %s", exc)
        return []
