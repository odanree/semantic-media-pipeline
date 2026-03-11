"""
ILLMProvider — Interface Segregation Principle.

All providers expose the same two methods: complete() and stream().
Callers (RAG pipeline, agents) depend on this Protocol, not on any
concrete SDK class.

Adding a new provider: create a new file in this package, implement the
Protocol, and register it in factory.py.
"""

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class ILLMProvider(Protocol):
    """Minimal interface for an LLM backend."""

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """Return the full completion string."""
        ...

    async def stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Yield completion tokens as they arrive."""
        ...
