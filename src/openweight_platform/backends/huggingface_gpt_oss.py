"""Metered Hugging Face Inference Providers adapter for GPT-OSS."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from huggingface_hub import InferenceClient

from openweight_platform.backends.base import GenerationResult, ModelBackend


class HuggingFaceInferenceConfigurationError(RuntimeError):
    """Raised when the public inference secret is not configured."""


class HuggingFaceInferenceError(RuntimeError):
    """Sanitized boundary for provider, quota, and timeout failures."""


class HuggingFaceGptOssBackend(ModelBackend):
    """Stream GPT-OSS final-answer content through one explicit HF provider.

    The adapter intentionally has no retry loop. ``InferenceClient`` receives a
    finite timeout, and the provider is explicit rather than automatically
    selected, keeping latency and metered-provider behavior bounded.
    """

    def __init__(
        self,
        *,
        token: str | None,
        model_id: str,
        provider: str,
        max_new_tokens: int,
        timeout_seconds: float,
        max_retries: int = 0,
        client_factory: Callable[..., Any] = InferenceClient,
    ) -> None:
        if max_retries != 0:
            raise ValueError("Hugging Face application retries must remain zero")
        self._token = token
        self._model_id = model_id
        self._provider = provider
        self._max_new_tokens = max_new_tokens
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        self._client: Any | None = None

    @property
    def model_name(self) -> str:
        return self._model_id

    def load(self) -> None:
        """Configure the client without making a remote inference request."""

        if not self._token:
            raise HuggingFaceInferenceConfigurationError(
                "Hugging Face inference is not configured"
            )
        if self._client is None:
            self._client = self._client_factory(
                model=self._model_id,
                provider=self._provider,
                token=self._token,
                timeout=self._timeout_seconds,
            )

    def generate(self, prompt: str) -> GenerationResult:
        return self.generate_stream(prompt, lambda _: None)

    def generate_stream(
        self,
        prompt: str,
        on_text: Callable[[str], None],
    ) -> GenerationResult:
        if self._client is None:
            raise HuggingFaceInferenceConfigurationError(
                "Hugging Face inference is not configured"
            )
        started = time.perf_counter()
        pieces: list[str] = []
        try:
            response: Iterable[Any] = self._client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=self._model_id,
                max_tokens=self._max_new_tokens,
                stream=True,
                temperature=0.1,
            )
            for event in response:
                choices = getattr(event, "choices", ())
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None)
                # Reasoning fields are deliberately ignored. Only the provider's
                # employee-facing chat content crosses this boundary.
                if isinstance(content, str) and content:
                    pieces.append(content)
                    on_text(content)
        except Exception as error:
            raise HuggingFaceInferenceError(
                "Hugging Face inference request failed"
            ) from error
        text = "".join(pieces).strip()
        if not text:
            raise HuggingFaceInferenceError(
                "Hugging Face inference returned no final answer"
            )
        return GenerationResult(
            text=text,
            input_tokens=0,
            output_tokens=0,
            generation_seconds=time.perf_counter() - started,
            peak_vram_gib=0.0,
        )

    def unload(self) -> None:
        self._client = None


__all__ = [
    "HuggingFaceGptOssBackend",
    "HuggingFaceInferenceConfigurationError",
    "HuggingFaceInferenceError",
]
