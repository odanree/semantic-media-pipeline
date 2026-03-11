"""
Local LLM provider — OpenAI-compatible endpoint (Ollama, LM Studio, vLLM).
Mirrors the existing LLM_PROVIDER=local pattern in ask.py.

Required env vars:
    LLM_BASE_URL   e.g. http://localhost:11434/v1
    LLM_MODEL      e.g. mistral, llama3.2, etc.
"""

import os
from typing import AsyncIterator

from openai import AsyncOpenAI


class LocalLLMProvider:
    def __init__(self) -> None:
        base_url = os.getenv("LLM_BASE_URL", "")
        if not base_url:
            raise RuntimeError(
                "LLM_BASE_URL must be set for local provider "
                "(e.g. http://localhost:11434/v1)"
            )
        self._client = AsyncOpenAI(base_url=base_url, api_key="not-needed")
        self._default_model = os.getenv("LLM_MODEL", "mistral")

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        resp = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    async def stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        async with self._client.chat.completions.stream(
            model=model or self._default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ) as stream:
            async for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    yield delta
