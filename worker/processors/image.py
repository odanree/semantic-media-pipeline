from .base import BaseProcessor
from .registry import register


class ImageProcessor(BaseProcessor):
    file_type = "image"
    extensions = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".gif"})
    hash_full_file = True

    def get_celery_task(self):
        from tasks import process_image  # deferred to avoid circular import
        return process_image


register(ImageProcessor())
