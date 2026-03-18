"""
ProcessorRegistry — maps file extensions and file_type strings to processor instances.
"""

from typing import Dict, Optional

from .base import BaseProcessor


class ProcessorRegistry:
    def __init__(self):
        self._by_extension: Dict[str, BaseProcessor] = {}
        self._by_file_type: Dict[str, BaseProcessor] = {}

    def register(self, processor: BaseProcessor) -> None:
        for ext in processor.extensions:
            if ext in self._by_extension:
                raise ValueError(
                    f"Extension {ext!r} already claimed by "
                    f"{self._by_extension[ext].file_type!r}"
                )
            self._by_extension[ext] = processor
        self._by_file_type[processor.file_type] = processor

    def get_by_extension(self, ext: str) -> Optional[BaseProcessor]:
        return self._by_extension.get(ext.lower())

    def get_by_file_type(self, file_type: str) -> Optional[BaseProcessor]:
        return self._by_file_type.get(file_type)

    @property
    def supported_extensions(self) -> frozenset:
        return frozenset(self._by_extension.keys())


_registry = ProcessorRegistry()


def register(processor: BaseProcessor) -> None:
    _registry.register(processor)


def get_registry() -> ProcessorRegistry:
    return _registry
