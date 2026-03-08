"""
Real-time updates endpoints (WebSocket)

Two endpoints:
  /ws/processing-status  — pushes DB status counts every 5 s (poll-based)
  /ws/media-updates      — pushes worker completion events via Redis pub/sub
                           (worker publishes to 'lumen:media_updates' channel)
"""

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

# Track active connections to prevent resource exhaustion
active_connections = {
    "media_updates": [],
    "processing_status": []
}

# ---------------------------------------------------------------------------
# DB helpers (sync — polled in a thread-pool executor)
# ---------------------------------------------------------------------------

_engine = None


def _get_db_session():
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


def _query_status() -> dict:
    """Return pipeline status counts: total, by_status, by_type."""
    db = _get_db_session()
    try:
        rows = db.execute(
            text(
                "SELECT processing_status, file_type, COUNT(*) AS n "
                "FROM media_files "
                "GROUP BY processing_status, file_type"
            )
        ).fetchall()
    finally:
        db.close()

    by_status = {"pending": 0, "processing": 0, "done": 0, "error": 0}
    by_type = {"images": 0, "videos": 0}
    total = 0

    for status, ftype, count in rows:
        total += count
        if status in by_status:
            by_status[status] += count
        if ftype == "image":
            by_type["images"] += count
        elif ftype == "video":
            by_type["videos"] += count

    return {"total": total, "by_status": by_status, "by_type": by_type}


# ---------------------------------------------------------------------------
# Background tasks (run as asyncio tasks per connection)
# ---------------------------------------------------------------------------

async def _poll_status(websocket: WebSocket, interval: int = 5):
    """
    Query DB every `interval` seconds and push stats to the WS client.
    Format matches StatusUpdate interface in useStatusUpdates.ts.
    """
    loop = asyncio.get_event_loop()
    while True:
        try:
            await asyncio.sleep(interval)
            stats = await loop.run_in_executor(None, _query_status)
            await websocket.send_json({"type": "status_update", "files": stats})
        except asyncio.CancelledError:
            break
        except Exception:
            break


async def _redis_listener(websocket: WebSocket, channel: str):
    """
    Subscribe to a Redis pub/sub channel and forward every message to the
    WS client.  Worker publishes JSON strings to 'lumen:media_updates'.
    """
    try:
        import redis.asyncio as aioredis  # already installed (redis==5.2.1)
    except ImportError:
        logger.error("redis.asyncio not available — media-updates WS will be heartbeat-only")
        return

    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    client = aioredis.from_url(redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    logger.info(f"Subscribed to Redis channel: {channel}")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    await websocket.send_json(data)
                except Exception as e:
                    logger.warning(f"Failed to forward Redis message: {e}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Redis listener error on {channel}: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await client.aclose()


async def _heartbeat(websocket: WebSocket, interval: int = 30):
    try:
        while True:
            await asyncio.sleep(interval)
            await websocket.send_json({"type": "heartbeat", "status": "alive"})
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Endpoint: /api/ws/processing-status
# ---------------------------------------------------------------------------

@router.websocket("/ws/processing-status")
async def websocket_processing_status(websocket: WebSocket):
    """
    Push pipeline status counts every 5 seconds.

    Message format:
      {"type": "status_update", "files": {"total": N,
        "by_status": {"pending":..., "processing":..., "done":..., "error":...},
        "by_type": {"images":..., "videos":...}}}

    Usage:
        const ws = new WebSocket('ws://localhost:8000/api/ws/processing-status');
        ws.onmessage = ({data}) => {
            const msg = JSON.parse(data);
            if (msg.files) setStatus(msg.files);  // StatusPanel logic
        };
    """
    await websocket.accept()
    active_connections["processing_status"].append(websocket)
    logger.info(f"WS/processing-status connected (total: {len(active_connections['processing_status'])})")

    # Send initial connection + immediate stats snapshot
    await websocket.send_json({"type": "status", "status": "ready",
                               "message": "Connected to processing status stream"})
    try:
        loop = asyncio.get_event_loop()
        snapshot = await loop.run_in_executor(None, _query_status)
        await websocket.send_json({"type": "status_update", "files": snapshot})
    except Exception as e:
        logger.warning(f"Initial snapshot failed: {e}")

    heartbeat_task = asyncio.create_task(_heartbeat(websocket, interval=30))
    poll_task = asyncio.create_task(_poll_status(websocket, interval=5))

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        logger.info("WS/processing-status client disconnected")
    except Exception as e:
        logger.error(f"WS/processing-status error: {e}")
    finally:
        heartbeat_task.cancel()
        poll_task.cancel()
        if websocket in active_connections["processing_status"]:
            active_connections["processing_status"].remove(websocket)
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Endpoint: /api/ws/media-updates
# ---------------------------------------------------------------------------

@router.websocket("/ws/media-updates")
async def websocket_media_updates(websocket: WebSocket):
    """
    Push individual file-completion events in real time via Redis pub/sub.
    The Celery worker publishes to 'lumen:media_updates' after each db.commit().

    Message format:
      {"channel": "media_processing", "id": "<uuid>",
       "file_path": "...", "file_type": "image|video",
       "status": "done", "processed_at": "<iso>"}

    Usage:
        const ws = new WebSocket('ws://localhost:8000/api/ws/media-updates');
        ws.onmessage = ({data}) => {
            const update = JSON.parse(data);
            if (update.channel === 'media_processing') { ... }
        };
    """
    await websocket.accept()
    active_connections["media_updates"].append(websocket)
    logger.info(f"WS/media-updates connected (total: {len(active_connections['media_updates'])})")

    await websocket.send_json({"type": "connection", "status": "connected",
                               "message": "Connected to media updates stream"})

    heartbeat_task = asyncio.create_task(_heartbeat(websocket, interval=30))
    redis_task = asyncio.create_task(_redis_listener(websocket, "lumen:media_updates"))

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        logger.info("WS/media-updates client disconnected")
    except Exception as e:
        logger.error(f"WS/media-updates error: {e}")
    finally:
        heartbeat_task.cancel()
        redis_task.cancel()
        if websocket in active_connections["media_updates"]:
            active_connections["media_updates"].remove(websocket)
        try:
            await websocket.close()
        except RuntimeError:
            pass

