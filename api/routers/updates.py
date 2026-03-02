"""
Real-time updates endpoints (WebSocket)

Note: PostgreSQL notification listener not yet implemented.
This is a placeholder for future real-time update functionality.
"""

import asyncio
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

# Track active connections to prevent resource exhaustion
active_connections = {
    "media_updates": [],
    "processing_status": []
}


@router.websocket("/ws/media-updates")
async def websocket_media_updates(websocket: WebSocket):
    """
    WebSocket endpoint for real-time media processing updates.
    
    Includes heartbeat mechanism and timeout handling to prevent
    resource exhaustion from dangling connections.
    
    Usage:
        const ws = new WebSocket('ws://localhost:8000/api/ws/media-updates');
        ws.onmessage = (event) => {
            const update = JSON.parse(event.data);
            console.log(update);
        };
    """
    await websocket.accept()
    logger.info(f"WebSocket client connected - media-updates (total: {len(active_connections['media_updates']) + 1})")
    active_connections["media_updates"].append(websocket)
    
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "message": "Connected to media updates stream"
        })
        
        # Heartbeat task to keep connection alive and detect dead clients
        heartbeat_task = asyncio.create_task(_heartbeat(websocket, interval=30))
        
        try:
            # Wait for client messages (but we don't process them)
            while True:
                try:
                    # Set receive timeout to detect hung connections
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                except asyncio.TimeoutError:
                    # Client hasn't sent anything in 60 seconds, but connection is still monitored
                    continue
        except WebSocketDisconnect:
            logger.info("Media updates client disconnected")
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
                
    except Exception as e:
        logger.error(f"WebSocket media-updates error: {e}")
    finally:
        if websocket in active_connections["media_updates"]:
            active_connections["media_updates"].remove(websocket)
        # Try to close the connection, but don't fail if it's already closed
        try:
            await websocket.close()
        except RuntimeError:
            # Connection already closed, this is expected
            pass


@router.websocket("/ws/processing-status")
async def websocket_processing_status(websocket: WebSocket):
    """
    WebSocket endpoint for real-time processing status updates.
    
    Streams status changes for media processing tasks with proper
    resource management and timeout handling.
    """
    await websocket.accept()
    logger.info(f"WebSocket client connected - processing-status (total: {len(active_connections['processing_status']) + 1})")
    active_connections["processing_status"].append(websocket)
    
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "status",
            "status": "ready",
            "message": "Connected to processing status stream"
        })
        
        # Heartbeat task to keep connection alive and detect dead clients
        heartbeat_task = asyncio.create_task(_heartbeat(websocket, interval=30))
        
        try:
            # Wait for client messages (but we don't process them)
            while True:
                try:
                    # Set receive timeout to detect hung connections
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                except asyncio.TimeoutError:
                    # Client hasn't sent anything in 60 seconds, but connection is still monitored
                    continue
        except WebSocketDisconnect:
            logger.info("Processing status client disconnected")
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
                
    except Exception as e:
        logger.error(f"WebSocket processing-status error: {e}")
    finally:
        if websocket in active_connections["processing_status"]:
            active_connections["processing_status"].remove(websocket)
        # Try to close the connection, but don't fail if it's already closed
        try:
            await websocket.close()
        except RuntimeError:
            # Connection already closed, this is expected
            pass


async def _heartbeat(websocket: WebSocket, interval: int = 30):
    """
    Send periodic heartbeat messages to keep connection alive and
    detect dead clients. Prevents "insufficient resource" errors from
    accumulating stale connections.
    
    Args:
        websocket: WebSocket connection
        interval: Heartbeat interval in seconds (default 30)
    """
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await websocket.send_json({
                    "type": "heartbeat",
                    "status": "alive"
                })
            except Exception:
                # Connection is dead, exit heartbeat task
                break
    except asyncio.CancelledError:
        pass
