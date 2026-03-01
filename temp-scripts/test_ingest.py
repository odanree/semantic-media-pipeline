#!/usr/bin/env python
"""Test the ingestion engine"""
import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000/api"

def test_health():
    """Test API health endpoint"""
    print("=" * 70)
    print("Testing API Health...")
    print("=" * 70)
    try:
        r = requests.get(f'{BASE_URL}/health', timeout=5)
        print(f"✓ Status: {r.status_code}")
        print(f"✓ Response: {json.dumps(r.json(), indent=2)}\n")
        return True
    except Exception as e:
        print(f"✗ Error: {e}\n")
        return False

def test_ingest():
    """Test ingest endpoint"""
    print("=" * 70)
    print("Testing Ingest Endpoint...")
    print("=" * 70)
    try:
        payload = {"media_root": "/data/media"}
        r = requests.post(f'{BASE_URL}/ingest', json=payload, timeout=10)
        print(f"✓ Status: {r.status_code}")
        response_data = r.json()
        print(f"✓ Response:")
        for key, val in response_data.items():
            print(f"  {key}: {val}")
        
        task_id = response_data.get('task_id')
        print(f"\n✓ Ingest task submitted with ID: {task_id}\n")
        return task_id
    except Exception as e:
        print(f"✗ Error: {e}\n")
        return None

def check_task_status(task_id):
    """Check task status"""
    print("=" * 70)
    print(f"Checking Task Status: {task_id}")
    print("=" * 70)
    try:
        r = requests.get(f'{BASE_URL}/task/{task_id}', timeout=5)
        print(f"✓ Status: {r.status_code}")
        data = r.json()
        print(f"✓ Task Status: {data.get('status')}")
        if data.get('result'):
            print(f"✓ Result: {json.dumps(data.get('result'), indent=2)}")
        if data.get('error'):
            print(f"✗ Error: {data.get('error')}")
        print()
        return data.get('status')
    except Exception as e:
        print(f"✗ Error: {e}\n")
        return None

if __name__ == '__main__':
    # Test health
    if not test_health():
        print("✗ API is not responding. Exiting.")
        sys.exit(1)
    
    # Test ingest
    task_id = test_ingest()
    if not task_id:
        print("✗ Failed to submit ingest task.")
        sys.exit(1)
    
    # Monitor task for a bit
    print("=" * 70)
    print("Monitoring Task Execution...")
    print("=" * 70)
    for i in range(12):  # Check for 60 seconds
        time.sleep(5)
        status = check_task_status(task_id)
        print(f"[{i+1}/12] Status: {status}")
        
        if status == 'SUCCESS':
            print("\n✓ Task completed successfully!")
            break
        elif status == 'FAILURE':
            print("\n✗ Task failed!")
            break
    
    print("\n" + "=" * 70)
    print("Test completed. Check worker logs for details:")
    print("  docker logs lumen-worker --follow")
    print("=" * 70)
