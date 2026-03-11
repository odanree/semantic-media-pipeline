"""
LLM provider factory — reads LLM_PROVIDER env var and returns the concrete
implementation. This is the only place that knows about all three providers.

LLM_PROVIDER = openai | azure | local   (default: openai)
"""

import os

from llm.base import ILLMProvider


def build_llm_provider() -> ILLMProvider:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        from llm.azure_provider import AzureOpenAIProvider
        return AzureOpenAIProvider()
    elif provider == "local":
        from llm.local_provider import LocalLLMProvider
        return LocalLLMProvider()
    else:
        from llm.openai_provider import OpenAIProvider
        return OpenAIProvider()
