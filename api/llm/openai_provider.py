"""
OpenAI provider — wraps the existing OpenAI SDK pattern already used in ask.py.
"""

import os
from typing import AsyncIterator

from openai import AsyncOpenAI


class OpenAIProvider:
    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set for OpenAI provider")
        self._client = AsyncOpenAI(api_key=api_key)
        self._default_model = os.getenv("LLM_MODEL", "gpt-4o-mini")

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
