"""
Test script for PostgreSQL LISTEN/NOTIFY system
Run this to verify triggers are installed and working
"""

import os
import asyncpg
import asyncio
import json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_ASYNC_URL",
    "postgresql+asyncpg://lumen_user:REDACTED_DB_PASSWORD@lumen-postgres:5432/lumen"
)

# Convert to standard PostgreSQL URL
DB_URL = DATABASE_URL.replace("+asyncpg", "")


async def test_notifications():
    """Listen for notifications and trigger update"""
    conn = await asyncpg.connect(DB_URL)
    
    print("="*70)
    print("🧪 Testing PostgreSQL LISTEN/NOTIFY Triggers")
    print("="*70)
    
    # Create listener task
    notifications = []
    
    async def listener(connection, pid, channel, payload):
        data = json.loads(payload)
        data['channel'] = channel
        notifications.append(data)
        print(f"\n✅ Received on '{channel}':")
        print(f"   File: {data.get('file_path', 'N/A').split('/')[-1]}")
        print(f"   Status: {data.get('status', 'N/A')}")
        print(f"   ID: {data.get('id', 'N/A')[:8]}...")
    
    # Subscribe to channels
    print("\n📡 Subscribing to channels...")
    await conn.add_listener('media_processing', listener)
    await conn.add_listener('vector_indexed', listener)
    print("   ✓ media_processing")
    print("   ✓ vector_indexed")
    
    # Get a test media file
    print("\n🔍 Finding a test media file...")
    test_row = await conn.fetchrow(
        "SELECT id, file_path, processing_status FROM media_files LIMIT 1;"
    )
    
    if not test_row:
        print("   ⚠️  No media files found. Skipping trigger test.")
        await conn.close()
        return
    
    test_id = test_row['id']
    test_file = test_row['file_path'].split('/')[-1]
    current_status = test_row['processing_status']
    
    print(f"   Found: {test_file}")
    print(f"   Current status: {current_status}")
    
    # Trigger 1: Update processing status
    print("\n📝 Test 1: Updating processing_status...")
    new_status = 'processing' if current_status != 'processing' else 'completed'
    await conn.execute(
        "UPDATE media_files SET processing_status = $1 WHERE id = $2;",
        new_status, test_id
    )
    print(f"   Updated to: {new_status}")
    
    # Wait for notification
    await asyncio.sleep(0.5)
    if notifications:
        print(f"   ✅ Trigger fired! Received {len(notifications)} notification(s)")
    else:
        print(f"   ❌ No notification received - triggers may not be installed")
    
    # Trigger 2: Update qdrant_point_id (if not already set)
    print("\n📝 Test 2: Setting qdrant_point_id...")
    current_point_id = await conn.fetchval(
        "SELECT qdrant_point_id FROM media_files WHERE id = $1;", test_id
    )
    
    if current_point_id is None:
        import uuid
        test_point_id = str(uuid.uuid4())
        await conn.execute(
            "UPDATE media_files SET qdrant_point_id = $1 WHERE id = $2;",
            test_point_id, test_id
        )
        print(f"   Set qdrant_point_id (first time)")
        
        # Wait for notification
        await asyncio.sleep(0.5)
        vector_notifications = [n for n in notifications if n.get('channel') == 'vector_indexed']
        if vector_notifications:
            print(f"   ✅ Vector trigger fired! {len(vector_notifications)} notification(s)")
        else:
            print(f"   ⚠️  No vector_indexed notification (ID already set on previous run)")
    else:
        print(f"   ⚠️  qdrant_point_id already set - skipping to avoid duplicate trigger")
    
    # Check trigger installation
    print("\n🔧 Trigger Installation Status:")
    triggers = await conn.fetch("""
        SELECT tgname, tgisinternal 
        FROM pg_trigger 
        WHERE tgrelid = 'media_files'::regclass;
    """)
    
    if triggers:
        for trig in triggers:
            status = "✓" if not trig['tgisinternal'] else "○"
            print(f"   {status} {trig['tgname']}")
    else:
        print("   ✗ No triggers found on media_files table")
    
    # Summary
    print("\n" + "="*70)
    if notifications:
        print(f"✅ SUCCESS: {len(notifications)} notifications received")
        print("\nNotification samples:")
        for i, n in enumerate(notifications[:2], 1):
            print(f"\n   ({i}) Channel: {n.get('channel')}")
            print(f"       Status: {n.get('status', n.get('qdrant_point_id', 'N/A')[:8])}")
    else:
        print("❌ FAILED: No notifications received")
        print("\nPossible causes:")
        print("   1. Triggers not installed (run: docker-compose restart postgres)")
        print("   2. PostgreSQL not accessible")
        print("   3. Database credentials incorrect")
    
    print("="*70 + "\n")
    await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(test_notifications())
    except KeyboardInterrupt:
        print("\nTest cancelled")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
