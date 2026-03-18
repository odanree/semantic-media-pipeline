from .base import BaseProcessor
from .registry import register


class VideoProcessor(BaseProcessor):
    file_type = "video"
    extensions = frozenset({".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm", ".m4v"})
    hash_full_file = False  # 8 KB header hash is unique enough for large video files

    def get_celery_task(self):
        from tasks import process_video  # deferred to avoid circular import
        return process_video


register(VideoProcessor())
