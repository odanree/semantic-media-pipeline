"""
Celery Application Configuration
"""

import os

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from celery import Celery

app = Celery(
    __name__,
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)

# Configuration
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,           # At-least-once delivery
    worker_prefetch_multiplier=1,  # One task at a time for long-running jobs
    task_track_started=True,       # Track when tasks start
    # -----------------------------------------------------------------------
    # Memory & lifecycle management
    # Each worker loads CLIP (~600MB) + FFmpeg buffers (~400MB per 4K video).
    # Recycle children after N tasks to release FFmpeg/PyTorch memory leaks.
    # Memory limit (KB) triggers recycle if a child grows past 1 GB.
    # -----------------------------------------------------------------------
    worker_max_tasks_per_child=int(os.getenv("CELERY_MAX_TASKS_PER_CHILD", "50")),
    worker_max_memory_per_child=int(os.getenv("CELERY_MAX_MEMORY_PER_CHILD", "1500000")),  # 1.5 GB in KB
)

# Configure task defaults (exponential backoff)
app.conf.task_autoretry_for = {}
app.conf.task_max_retries = 5

# Import tasks module to register @app.task decorated functions
# Database initialization is now deferred to first use (lazy loading in db.session)
# so this import won't cause issues
try:
    import tasks  # noqa: F401 - imported for side effects (task registration)
except ImportError as e:
    print(f"Warning: Could not import tasks module: {e}")


