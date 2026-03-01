"""
WebSocket endpoints for real-time media processing updates
Streams PostgreSQL notifications to connected dashboard clients
"""

import os
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from api.utils.notifications import MediaNotificationListener

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

DATABASE_ASYNC_URL = os.getenv(
    "DATABASE_ASYNC_URL",
    "postgresql+asyncpg://lumen_user:lumen_secure_password_2026@lumen-postgres:5432/lumen"
)


@router.websocket("/ws/media-updates")
async def websocket_media_updates(websocket: WebSocket):
    """
    WebSocket endpoint for real-time media processing updates
    
    Broadcast channels:
    - media_processing: Status changes (pending -> processing -> completed/failed)
    - vector_indexed: Vector embeddings completed and indexed in Qdrant
    
    Client side (JavaScript):
        const ws = new WebSocket('ws://localhost:8000/api/ws/media-updates');
        ws.onmessage = (event) => {
            const update = JSON.parse(event.data);
            console.log(`${update.channel}: ${update.status || 'indexed'}`);
        };
    """
    await websocket.accept()
    listener = MediaNotificationListener(DATABASE_ASYNC_URL.replace("+asyncpg", ""))
    
    try:
        await listener.connect()
        logger.info(f"WebSocket client connected - listening to media updates")
        
        async for notification in listener.stream():
            try:
                await websocket.send_json(notification)
            except Exception as e:
                logger.error(f"Failed to send notification to client: {e}")
                break
    
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close(code=1000, reason=str(e))
    finally:
        await listener.disconnect()


@router.websocket("/ws/processing-status")
async def websocket_processing_status(websocket: WebSocket):
    """
    WebSocket endpoint filtered to only media processing status updates
    
    Minimal bandwidth - excludes vector indexing notifications
    """
    await websocket.accept()
    listener = MediaNotificationListener(
        DATABASE_ASYNC_URL.replace("+asyncpg", ""),
        channels=['media_processing']
    )
    
    try:
        await listener.connect()
        logger.info("WebSocket client connected - listening to processing status")
        
        async for notification in listener.stream():
            try:
                # Forward notification payload with processed metadata
                await websocket.send_json({
                    'type': notification.get('status'),
                    'id': notification.get('id'),
                    'file_path': notification.get('file_path'),
                    'error': notification.get('error_message'),
                    'completed_at': notification.get('processed_at'),
                })
            except Exception as e:
                logger.error(f"Failed to send update to client: {e}")
                break
    
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await listener.disconnect()
