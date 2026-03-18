"""
Processor registry package.

Importing this package registers all built-in processors.
To add a new file type: create a processor module, implement BaseProcessor,
call register(), then add an import below.
"""

from .base import BaseProcessor
from .registry import register, get_registry

from . import image  # noqa: F401 — triggers ImageProcessor registration
from . import video  # noqa: F401 — triggers VideoProcessor registration
# Future processors:
# from . import document  # noqa: F401

__all__ = ["BaseProcessor", "register", "get_registry"]
