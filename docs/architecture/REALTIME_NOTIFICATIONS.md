# Real-Time Media Processing Notifications

## Overview

The Lumen pipeline includes a **PostgreSQL LISTEN/NOTIFY** system that broadcasts real-time updates when media is processed or embedded. This enables:

- ✅ Live dashboard updates (WebSocket streaming)
- ✅ External webhook triggers (Lambda, Cloud Functions, etc.)
- ✅ Event-driven downstream processing
- ✅ Progress reporting without polling

## Architecture

### 1. Database Layer (PostgreSQL)

Two **trigger functions** fire when media status changes:

#### `media_processing` Channel
Fires when `processing_status` changes (pending → processing → completed/failed)

```json
{
  "id": "839cdf1e-41b9-49f8-8a08-028de1dd0ed2",
  "file_path": "/data/media/Pre-Dec 2025/photo.jpg",
  "file_type": "image",
  "status": "completed",
  "error_message": null,
  "processed_at": "2026-03-01T08:15:23.456789+00:00"
}
```

#### `vector_indexed` Channel
Fires when `qdrant_point_id` is set (embedding complete)

```json
{
  "id": "839cdf1e-41b9-49f8-8a08-028de1dd0ed2",
  "file_path": "/data/media/Pre-Dec 2025/photo.jpg",
  "qdrant_point_id": "abc12345-def6-7890-ghij-klmnopqrstu",
  "vector_indexed_at": "2026-03-01T08:15:25.123456+00:00"
}
```

### 2. Backend (FastAPI)

The API exposes **two WebSocket endpoints**:

| Endpoint | Channel | Use Case | Payload Size |
|----------|---------|----------|-------------|
| `/api/ws/media-updates` | Both | Full details, dashboard | ~500 bytes/update |
| `/api/ws/processing-status` | processing only | Status-only, lightweight | ~150 bytes/update |

**Implementation: `api/routers/updates.py`**

```python
@router.websocket("/ws/media-updates")
async def websocket_media_updates(websocket: WebSocket):
    """Real-time media processing + vector indexing updates"""
    # Connects to PostgreSQL, streams notifications to client
```

### 3. Frontend (Next.js)

React hook for consuming WebSocket updates:

**Location: `frontend/hooks/useMediaUpdates.ts`**

```typescript
// Listen and track updates
const { updates, isConnected } = useMediaUpdates(
  `${process.env.NEXT_PUBLIC_API_URL}/ws/media-updates`
);

// Use in component
<MediaUpdatesFeed />
<MediaProcessingDashboard />
```

## Usage Examples

### FastAPI Backend (Python)

```python
from api.utils.notifications import MediaNotificationListener
import asyncio

async def main():
    listener = MediaNotificationListener(
        db_url="postgresql://user:pass@host/lumen",
        channels=['media_processing', 'vector_indexed']
    )
    
    async with listener.listen():
        async for notification in listener.stream():
            print(f"📬 {notification['channel']}: {notification['status']}")

asyncio.run(main())
```

### Frontend (React/TypeScript)

```tsx
import { useMediaUpdates, MediaUpdatesFeed } from '@/hooks/useMediaUpdates';

export function DashboardPage() {
  const { updates, isConnected } = useMediaUpdates(
    `${process.env.NEXT_PUBLIC_API_URL}/ws/media-updates`
  );

  return (
    <div>
      <h2>Pipeline Live Feed</h2>
      {isConnected && <span className="text-green-600">● Live</span>}
      
      <div className="space-y-2">
        {updates.map((u) => (
          <div key={u.id}>
            <p>{u.file_path}</p>
            <p className="text-sm text-gray-600">Status: {u.status}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
```

### External Webhook (Node.js)

```javascript
// Consume notifications via REST API or WebSocket
const ws = new WebSocket('ws://localhost:8000/api/ws/media-updates');

ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  
  if (update.status === 'completed') {
    // Trigger downstream processing
    fetch('/api/webhook/video-transcoding', {
      method: 'POST',
      body: JSON.stringify({ 
        file_id: update.id,
        file_path: update.file_path 
      })
    });
  }
};
```

## Database Schema Changes

Added to `scripts/init-db.sql`:

```sql
-- Notification function for processing status changes
CREATE FUNCTION notify_processing_status() RETURNS trigger AS $$
BEGIN
  IF (OLD IS NULL) OR (OLD.processing_status IS DISTINCT FROM NEW.processing_status) THEN
    PERFORM pg_notify('media_processing', json_build_object(
      'id', NEW.id::text,
      'file_path', NEW.file_path,
      'status', NEW.processing_status,
      'error_message', NEW.error_message,
      'processed_at', NEW.processed_at
    )::text);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach trigger to media_files table
CREATE TRIGGER media_processing_trigger
AFTER INSERT OR UPDATE ON media_files
FOR EACH ROW EXECUTE FUNCTION notify_processing_status();
```

## Performance Considerations

| Scenario | Behavior |
|----------|----------|
| 1 concurrent client | ~1 WebSocket conn, minimal overhead |
| 10 concurrent clients | PostgreSQL NOTIFY is broadcast, no amplification |
| Bulk updates (1000 items) | Each fires trigger independently (acceptable for real-time use) |
| High-loss network | App-level reconnect with exponential backoff |

**PostgreSQL LISTEN is not persistent** - clients reconnect/resubscribe on disconnect.

## Troubleshooting

### WebSocket not connecting
- Verify CORS is enabled in FastAPI
- Check `DATABASE_ASYNC_URL` in `.env`
- Ensure PostgreSQL is accessible from API container

### No notifications received
- Check trigger is installed: `SELECT * FROM pg_trigger;`
- Verify function exists: `\df notify_processing_status` in psql
- Monitor: `LISTEN media_processing;` then `UPDATE media_files SET processing_status='processing';`

### Too many WebSocket connections
- Use the lighter `/wsmedia/processing-status` endpoint (processing only)
- Implement client-side message throttling
- Add connection pooling in production

## Future Enhancements

- [ ] Redis pub/sub for multi-instance deployments
- [ ] Webhook service with retry logic
- [ ] Notification filtering by file type, status
- [ ] Event history API (last 1000 events)
- [ ] Prometheus metrics for notification lag
