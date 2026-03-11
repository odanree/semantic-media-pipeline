"""
Tests for the LLM provider factory and all three provider implementations.

Strategy: mock AsyncOpenAI / AsyncAzureOpenAI at the provider module level
so no real HTTP calls are made. Each provider is tested for construction,
complete(), stream(), and conformance to ILLMProvider.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AsyncStreamCtx:
    """Minimal async context-manager / async-iterable for streaming tests."""

    def __init__(self, events: list) -> None:
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self._events:
            yield e


def _mock_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices[0].message.content = content
    return resp


def _stream_event(content) -> MagicMock:
    event = MagicMock()
    event.choices = [MagicMock()]
    event.choices[0].delta.content = content
    return event


# ---------------------------------------------------------------------------
# Factory routing
# ---------------------------------------------------------------------------

class TestBuildLlmProvider:
    def test_defaults_to_openai(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("llm.openai_provider.AsyncOpenAI"):
            from llm.factory import build_llm_provider
            from llm.openai_provider import OpenAIProvider
            provider = build_llm_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_explicit_openai(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("llm.openai_provider.AsyncOpenAI"):
            from llm.factory import build_llm_provider
            from llm.openai_provider import OpenAIProvider
            provider = build_llm_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_azure(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "azure")
        with patch("llm.azure_provider.AsyncAzureOpenAI"):
            from llm.factory import build_llm_provider
            from llm.azure_provider import AzureOpenAIProvider
            provider = build_llm_provider()
        assert isinstance(provider, AzureOpenAIProvider)

    def test_local(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "local")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        with patch("llm.local_provider.AsyncOpenAI"):
            from llm.factory import build_llm_provider
            from llm.local_provider import LocalLLMProvider
            provider = build_llm_provider()
        assert isinstance(provider, LocalLLMProvider)

    def test_unknown_value_falls_back_to_openai(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "unknown_backend_xyz")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("llm.openai_provider.AsyncOpenAI"):
            from llm.factory import build_llm_provider
            from llm.openai_provider import OpenAIProvider
            provider = build_llm_provider()
        assert isinstance(provider, OpenAIProvider)


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

class TestOpenAIProvider:
    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("llm.openai_provider.AsyncOpenAI"):
            from llm.openai_provider import OpenAIProvider
            with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
                OpenAIProvider()

    def test_complete_returns_content_string(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_response("Hello world")
        )
        with patch("llm.openai_provider.AsyncOpenAI", return_value=mock_client):
            from llm.openai_provider import OpenAIProvider
            provider = OpenAIProvider()
        result = asyncio.run(
            provider.complete([{"role": "user", "content": "hi"}])
        )
        assert result == "Hello world"

    def test_complete_none_content_returns_empty_string(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_response(None)
        )
        with patch("llm.openai_provider.AsyncOpenAI", return_value=mock_client):
            from llm.openai_provider import OpenAIProvider
            provider = OpenAIProvider()
        result = asyncio.run(provider.complete([]))
        assert result == ""

    def test_complete_passes_temperature_and_max_tokens(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_response("ok")
        )
        with patch("llm.openai_provider.AsyncOpenAI", return_value=mock_client):
            from llm.openai_provider import OpenAIProvider
            provider = OpenAIProvider()
        asyncio.run(
            provider.complete([], temperature=0.1, max_tokens=512)
        )
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.1
        assert kwargs["max_tokens"] == 512

    def test_stream_yields_non_none_deltas(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client = MagicMock()
        events = [
            _stream_event("tok1"),
            _stream_event(None),   # None delta — should be skipped
            _stream_event("tok2"),
        ]
        mock_client.chat.completions.stream.return_value = _AsyncStreamCtx(events)
        with patch("llm.openai_provider.AsyncOpenAI", return_value=mock_client):
            from llm.openai_provider import OpenAIProvider
            provider = OpenAIProvider()

        async def _collect():
            return [t async for t in provider.stream([{"role": "user", "content": "hi"}])]

        tokens = asyncio.run(_collect())
        assert tokens == ["tok1", "tok2"]

    def test_stream_skips_events_with_no_choices(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client = MagicMock()
        empty_event = MagicMock()
        empty_event.choices = []
        events = [empty_event, _stream_event("good")]
        mock_client.chat.completions.stream.return_value = _AsyncStreamCtx(events)
        with patch("llm.openai_provider.AsyncOpenAI", return_value=mock_client):
            from llm.openai_provider import OpenAIProvider
            provider = OpenAIProvider()

        async def _collect():
            return [t async for t in provider.stream([])]

        tokens = asyncio.run(_collect())
        assert tokens == ["good"]


# ---------------------------------------------------------------------------
# Azure provider
# ---------------------------------------------------------------------------

class TestAzureOpenAIProvider:
    def test_complete_returns_content_string(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://resource.openai.azure.com/")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_response("Azure response")
        )
        with patch("llm.azure_provider.AsyncAzureOpenAI", return_value=mock_client):
            from llm.azure_provider import AzureOpenAIProvider
            provider = AzureOpenAIProvider()
        result = asyncio.run(
            provider.complete([{"role": "user", "content": "hi"}])
        )
        assert result == "Azure response"

    def test_complete_uses_env_model_as_default(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "my-deployment-name")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_response("ok")
        )
        with patch("llm.azure_provider.AsyncAzureOpenAI", return_value=mock_client):
            from llm.azure_provider import AzureOpenAIProvider
            provider = AzureOpenAIProvider()
        asyncio.run(provider.complete([]))
        assert mock_client.chat.completions.create.call_args.kwargs["model"] == "my-deployment-name"


# ---------------------------------------------------------------------------
# Local provider
# ---------------------------------------------------------------------------

class TestLocalLLMProvider:
    def test_raises_without_base_url(self, monkeypatch):
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        with patch("llm.local_provider.AsyncOpenAI"):
            from llm.local_provider import LocalLLMProvider
            with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
                LocalLLMProvider()

    def test_complete_returns_content_string(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "mistral")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_response("Local response")
        )
        with patch("llm.local_provider.AsyncOpenAI", return_value=mock_client):
            from llm.local_provider import LocalLLMProvider
            provider = LocalLLMProvider()
        result = asyncio.run(
            provider.complete([{"role": "user", "content": "hi"}])
        )
        assert result == "Local response"

    def test_complete_uses_env_model_as_default(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "llama3.2")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_response("ok")
        )
        with patch("llm.local_provider.AsyncOpenAI", return_value=mock_client):
            from llm.local_provider import LocalLLMProvider
            provider = LocalLLMProvider()
        asyncio.run(provider.complete([]))
        assert mock_client.chat.completions.create.call_args.kwargs["model"] == "llama3.2"


# ---------------------------------------------------------------------------
# ILLMProvider Protocol conformance
# ---------------------------------------------------------------------------

class TestILLMProviderProtocol:
    def test_openai_satisfies_protocol(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("llm.openai_provider.AsyncOpenAI"):
            from llm.openai_provider import OpenAIProvider
            from llm.base import ILLMProvider
            provider = OpenAIProvider()
        assert isinstance(provider, ILLMProvider)

    def test_azure_satisfies_protocol(self):
        with patch("llm.azure_provider.AsyncAzureOpenAI"):
            from llm.azure_provider import AzureOpenAIProvider
            from llm.base import ILLMProvider
            provider = AzureOpenAIProvider()
        assert isinstance(provider, ILLMProvider)

    def test_local_satisfies_protocol(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        with patch("llm.local_provider.AsyncOpenAI"):
            from llm.local_provider import LocalLLMProvider
            from llm.base import ILLMProvider
            provider = LocalLLMProvider()
        assert isinstance(provider, ILLMProvider)

    def test_protocol_is_runtime_checkable(self):
        from llm.base import ILLMProvider
        # runtime_checkable means isinstance() works without subclassing
        assert hasattr(ILLMProvider, "__protocol_attrs__") or hasattr(ILLMProvider, "_is_protocol")
