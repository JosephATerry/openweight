"""Central construction for supported local model backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from openweight_platform.backends.base import ModelBackend
from openweight_platform.backends.muse_glimmer import (
    DEFAULT_MUSE_BASE_URL,
    DEFAULT_MUSE_MODEL_ALIAS,
    DEFAULT_MUSE_TIMEOUT_SECONDS,
    ReasoningStrength,
    SUPPORTED_REASONING_STRENGTHS,
)


DEFAULT_BACKEND_NAME = "gpt-oss"
DEFAULT_GPT_OSS_MODEL_ID = "openai/gpt-oss-20b"
BackendName: TypeAlias = Literal["gpt-oss", "muse-glimmer"]
InferenceMode: TypeAlias = Literal["local", "huggingface"]
SUPPORTED_BACKENDS = ("gpt-oss", "muse-glimmer")
SUPPORTED_INFERENCE_MODES = ("local", "huggingface")


@dataclass(frozen=True)
class ModelBackendConfig:
    backend_name: BackendName = "gpt-oss"
    max_new_tokens: int = 256
    gpt_oss_model_id: str = DEFAULT_GPT_OSS_MODEL_ID
    muse_base_url: str = DEFAULT_MUSE_BASE_URL
    muse_model_alias: str = DEFAULT_MUSE_MODEL_ALIAS
    muse_timeout_seconds: float = DEFAULT_MUSE_TIMEOUT_SECONDS
    reasoning_strength: ReasoningStrength = "low"
    muse_response_grammar: str | None = None
    inference_mode: InferenceMode = "local"
    huggingface_token: str | None = field(default=None, repr=False)
    huggingface_provider: str = "groq"
    huggingface_timeout_seconds: float = 60.0
    huggingface_max_retries: int = 0

    def __post_init__(self) -> None:
        if self.backend_name not in SUPPORTED_BACKENDS:
            expected = ", ".join(SUPPORTED_BACKENDS)
            raise ValueError(f"backend_name must be one of: {expected}")
        if self.inference_mode not in SUPPORTED_INFERENCE_MODES:
            expected = ", ".join(SUPPORTED_INFERENCE_MODES)
            raise ValueError(f"inference_mode must be one of: {expected}")
        if self.inference_mode == "huggingface" and self.backend_name != "gpt-oss":
            raise ValueError("Hugging Face inference mode requires gpt-oss")
        if not isinstance(self.max_new_tokens, int) or isinstance(
            self.max_new_tokens,
            bool,
        ):
            raise TypeError("max_new_tokens must be an integer")
        if self.max_new_tokens <= 0:
            raise ValueError("max_new_tokens must be greater than zero")
        if self.reasoning_strength not in SUPPORTED_REASONING_STRENGTHS:
            expected = ", ".join(SUPPORTED_REASONING_STRENGTHS)
            raise ValueError(
                f"reasoning_strength must be one of: {expected}"
            )
        if self.muse_response_grammar is not None and (
            not isinstance(self.muse_response_grammar, str)
            or not self.muse_response_grammar.strip()
        ):
            raise ValueError(
                "muse_response_grammar must be non-empty when provided"
            )
        if self.huggingface_timeout_seconds <= 0:
            raise ValueError("huggingface_timeout_seconds must be greater than zero")
        if self.huggingface_max_retries != 0:
            raise ValueError("Hugging Face application retries must remain zero")


def build_model_backend(config: ModelBackendConfig) -> ModelBackend:
    """Build one backend while keeping heavyweight imports selection-local."""
    if config.inference_mode == "huggingface":
        from openweight_platform.backends.huggingface_gpt_oss import (
            HuggingFaceGptOssBackend,
        )

        return HuggingFaceGptOssBackend(
            token=config.huggingface_token,
            model_id=config.gpt_oss_model_id,
            provider=config.huggingface_provider,
            max_new_tokens=config.max_new_tokens,
            timeout_seconds=config.huggingface_timeout_seconds,
            max_retries=config.huggingface_max_retries,
        )
    if config.backend_name == "gpt-oss":
        from openweight_platform.backends.gpt_oss import GptOssBackend

        return GptOssBackend(
            model_id=config.gpt_oss_model_id,
            max_new_tokens=config.max_new_tokens,
        )

    if config.backend_name == "muse-glimmer":
        from openweight_platform.backends.muse_glimmer import MuseGlimmerBackend

        return MuseGlimmerBackend(
            base_url=config.muse_base_url,
            model_alias=config.muse_model_alias,
            max_new_tokens=config.max_new_tokens,
            timeout_seconds=config.muse_timeout_seconds,
            reasoning_strength=config.reasoning_strength,
            response_grammar=config.muse_response_grammar,
        )

    raise ValueError(f"Unsupported model backend: {config.backend_name!r}")


__all__ = [
    "BackendName",
    "DEFAULT_BACKEND_NAME",
    "DEFAULT_GPT_OSS_MODEL_ID",
    "InferenceMode",
    "ModelBackendConfig",
    "SUPPORTED_BACKENDS",
    "SUPPORTED_INFERENCE_MODES",
    "build_model_backend",
]
