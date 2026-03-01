"""
PostgreSQL LISTEN/NOTIFY client for real-time media processing updates
Consumes database notifications for dashboard streaming and external webhooks
"""

import asyncio
import json
import logging
from typing import Callable, Optional
import asyncpg
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class MediaNotificationListener:
    """
    Listen for real-time PostgreSQL notifications on media processing and vector indexing
    
    Usage:
        listener = MediaNotificationListener(db_url)
        
        async with listener.listen('media_processing') as conn:
            async for notification in listener.stream():
                # Handle {id, file_path, status, error_message, processed_at}
                await process_update(notification)
    """
    
    def __init__(
        self,
        db_url: str,
        channels: list[str] | None = None,
    ):
        """
        Args:
            db_url: PostgreSQL connection string (e.g., 'postgresql://user:pass@host/db')
            channels: List of channels to listen on. Defaults to all: 
                     ['media_processing', 'vector_indexed']
        """
        self.db_url = db_url
        self.channels = channels or ['media_processing', 'vector_indexed']
        self.connection: Optional[asyncpg.Connection] = None
        self._notifications_queue: asyncio.Queue = asyncio.Queue()
    
    async def connect(self) -> None:
        """Establish database connection and subscribe to channels"""
        try:
            self.connection = await asyncpg.connect(self.db_url)
            logger.info(f"Connected to PostgreSQL for notifications")
            
            for channel in self.channels:
                await self.connection.add_listener(channel, self._on_notification)
                logger.info(f"Listening on channel: {channel}")
        except Exception as e:
            logger.error(f"Failed to connect for notifications: {e}")
            raise
    
    async def disconnect(self) -> None:
        """Close database connection"""
        if self.connection:
            await self.connection.close()
            logger.info("Disconnected from PostgreSQL")
    
    def _on_notification(self, connection, pid, channel, payload):
        """Internal callback triggered by PostgreSQL NOTIFY"""
        try:
            data = json.loads(payload)
            data['channel'] = channel
            data['pid'] = pid
            # Queue the notification for async iteration
            self.connection.get_event_loop().call_soon_threadsafe(
                self._notifications_queue.put_nowait, data
            )
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in notification: {payload}")
        except Exception as e:
            logger.error(f"Error processing notification: {e}")
    
    async def stream(self):
        """
        Async generator that yields notifications as they arrive
        
        Example:
            async for event in listener.stream():
                print(f"{event['channel']}: {event['status']}")
        """
        while True:
            try:
                notification = await asyncio.wait_for(
                    self._notifications_queue.get(),
                    timeout=60.0  # Connection heartbeat
                )
                yield notification
            except asyncio.TimeoutError:
                # Periodic heartbeat - connection still alive
                logger.debug("Notification listener heartbeat")
                continue
            except asyncio.CancelledError:
                logger.info("Notification listener cancelled")
                break
            except Exception as e:
                logger.error(f"Error in notification stream: {e}")
                break
    
    @asynccontextmanager
    async def listen(self, *channels: str):
        """
        Context manager for listening on specific channels
        
        Example:
            async with listener.listen('media_processing', 'vector_indexed'):
                async for notification in listener.stream():
                    ...
        """
        old_channels = self.channels
        try:
            if channels:
                # Temporarily override channels for this context
                self.channels = list(channels)
            await self.connect()
            yield self.connection
        finally:
            await self.disconnect()
            self.channels = old_channels


# Example integration with FastAPI WebSocket
async def broadcast_media_updates(
    listener: MediaNotificationListener,
    broadcast_func: Callable,
):
    """
    Stream database notifications to WebSocket clients
    
    Example in FastAPI:
        @app.websocket("/ws/media-updates")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            listener = MediaNotificationListener(DATABASE_URL)
            
            async def broadcast(data):
                await websocket.send_json(data)
            
            try:
                await broadcast_media_updates(listener, broadcast)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                await websocket.close()
    """
    async with listener.listen('media_processing', 'vector_indexed'):
        async for notification in listener.stream():
            await broadcast_func(notification)


if __name__ == "__main__":
    # Example usage - listen for all notifications
    import os
    
    db_url = os.getenv(
        "DATABASE_ASYNC_URL",
        "postgresql://lumen_user:lumen_secure_password_2026@localhost/lumen"
    )
    
    async def main():
        listener = MediaNotificationListener(db_url)
        async with listener.listen():
            async for notification in listener.stream():
                print(f"📬 {notification['channel']}: {json.dumps(notification, indent=2)}")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nListener stopped")
