"""
Azure OpenAI provider — uses the AzureOpenAI client from the openai SDK.

Required env vars:
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_ENDPOINT      e.g. https://my-resource.openai.azure.com/
    AZURE_OPENAI_API_VERSION   e.g. 2024-02-01
    LLM_MODEL                  Azure deployment name (e.g. gpt-4o)
"""

import os
from typing import AsyncIterator

from openai import AsyncAzureOpenAI


class AzureOpenAIProvider:
    def __init__(self) -> None:
        self._client = AsyncAzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
        self._default_model = os.getenv("LLM_MODEL", "gpt-4o")

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
