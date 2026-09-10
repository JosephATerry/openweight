from __future__ import annotations

from types import SimpleNamespace

import pytest

from openweight_platform.backends.huggingface_gpt_oss import (
    HuggingFaceGptOssBackend,
    HuggingFaceInferenceConfigurationError,
    HuggingFaceInferenceError,
)


class FakeClient:
    def __init__(self, events=None, error: Exception | None = None):
        self.events = events or []
        self.error = error
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return iter(self.events)


def event(*, content: str | None = None, reasoning: str | None = None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, reasoning=reasoning)
            )
        ]
    )


def test_missing_token_fails_before_client_construction() -> None:
    constructed = []
    backend = HuggingFaceGptOssBackend(
        token=None,
        model_id="openai/gpt-oss-20b",
        provider="groq",
        max_new_tokens=768,
        timeout_seconds=60,
        client_factory=lambda **kwargs: constructed.append(kwargs),
    )

    with pytest.raises(HuggingFaceInferenceConfigurationError):
        backend.load()

    assert constructed == []


def test_remote_backend_is_lazy_bounded_and_streams_final_content_only() -> None:
    client = FakeClient(
        [
            event(reasoning="private analysis", content=None),
            event(content="Grounded "),
            event(content="answer [EPG-ACCESS-001#1]."),
        ]
    )
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return client

    backend = HuggingFaceGptOssBackend(
        token="unit-test-token",  # pragma: allowlist secret
        model_id="openai/gpt-oss-20b",
        provider="groq",
        max_new_tokens=768,
        timeout_seconds=42,
        client_factory=factory,
    )
    streamed = []

    backend.load()
    assert client.calls == []
    result = backend.generate_stream("grounded prompt", streamed.append)

    assert factory_calls == [{
        "model": "openai/gpt-oss-20b",
        "provider": "groq",
        "token": "unit-test-token",  # pragma: allowlist secret
        "timeout": 42,
    }]
    assert len(client.calls) == 1
    assert client.calls[0]["stream"] is True
    assert client.calls[0]["max_tokens"] == 768
    assert result.text == "Grounded answer [EPG-ACCESS-001#1]."
    assert "".join(streamed) == result.text
    assert "private analysis" not in result.text


def test_provider_failure_is_sanitized_and_never_retried() -> None:
    client = FakeClient(error=RuntimeError("provider leaked private detail"))
    backend = HuggingFaceGptOssBackend(
        token="unit-test-token",  # pragma: allowlist secret
        model_id="openai/gpt-oss-20b",
        provider="groq",
        max_new_tokens=768,
        timeout_seconds=60,
        client_factory=lambda **_: client,
    )
    backend.load()

    with pytest.raises(
        HuggingFaceInferenceError,
        match="Hugging Face inference request failed",
    ) as captured:
        backend.generate("grounded prompt")

    assert len(client.calls) == 1
    assert "private detail" not in str(captured.value)


def test_nonzero_application_retries_are_rejected() -> None:
    with pytest.raises(ValueError, match="retries"):
        HuggingFaceGptOssBackend(
            token="unit-test-token",  # pragma: allowlist secret
            model_id="openai/gpt-oss-20b",
            provider="groq",
            max_new_tokens=768,
            timeout_seconds=60,
            max_retries=1,
        )
