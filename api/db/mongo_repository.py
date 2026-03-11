"""
MongoDB / Azure CosmosDB (MongoDB wire protocol) implementation of MediaRepository.

Key points for JD1 (Azure) alignment:
  - Azure CosmosDB for MongoDB accepts the *same motor driver* with a different
    connection string: MONGO_URI=mongodb://<account>:<key>@<account>.mongo.cosmos.azure.com:10255/?ssl=true
  - Demonstrates multi-database skill without changing application code
  - Uses motor (async MongoDB driver) — install: pip install motor

Toggle: DB_BACKEND=mongodb in environment
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from api.db.repository import MediaRepository  # noqa: F401 (re-export for type-checkers)


MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "lumen")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "media_files")


class MongoDBMediaRepository:
    """
    Async MongoDB implementation of the MediaRepository Protocol.

    Compatible with:
      - Local MongoDB 6+
      - Azure CosmosDB for MongoDB
      - MongoDB Atlas

    Each document mirrors the PostgreSQL MediaFile schema:
      { _id: int, file_path: str, file_type: str, embedding_model: str, ... }
    """

    def __init__(self) -> None:
        self._client = None
        self._collection = None

    def _get_collection(self):
        if self._collection is None:
            import motor.motor_asyncio as motor  # type: ignore[import]
            self._client = motor.AsyncIOMotorClient(MONGO_URI)
            self._collection = self._client[MONGO_DB][MONGO_COLLECTION]
        return self._collection

    async def get_by_id(self, media_id: int) -> Optional[Dict[str, Any]]:
        col = self._get_collection()
        doc = await col.find_one({"_id": media_id})
        return _normalize(doc) if doc else None

    async def search_by_metadata(
        self,
        filters: Dict[str, Any],
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        col = self._get_collection()
        cursor = col.find(filters).limit(limit)
        return [_normalize(doc) async for doc in cursor]

    async def upsert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        col = self._get_collection()
        doc = dict(record)
        doc_id = doc.pop("id", None)
        if doc_id is not None:
            doc["_id"] = doc_id

        await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        saved = await col.find_one({"_id": doc["_id"]})
        return _normalize(saved)

    async def delete(self, media_id: int) -> bool:
        col = self._get_collection()
        result = await col.delete_one({"_id": media_id})
        return result.deleted_count > 0


def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Map Mongo _id → id to keep a consistent interface with the Postgres repo."""
    if doc is None:
        return {}
    out = dict(doc)
    if "_id" in out:
        out["id"] = out.pop("_id")
    return out
