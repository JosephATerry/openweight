from __future__ import annotations

import pytest

from openweight_platform.backends.factory import (
    DEFAULT_BACKEND_NAME,
    DEFAULT_GPT_OSS_MODEL_ID,
    ModelBackendConfig,
    build_model_backend,
)
from openweight_platform.backends.gpt_oss import GptOssBackend
from openweight_platform.backends.huggingface_gpt_oss import HuggingFaceGptOssBackend
from openweight_platform.backends.muse_glimmer import MuseGlimmerBackend
from openweight_platform.benchmarking.policy_constraints import (
    MUSE_POLICY_DECISION_GBNF,
)


def test_factory_defaults_to_existing_gpt_oss_construction() -> None:
    config = ModelBackendConfig()

    backend = build_model_backend(config)

    assert DEFAULT_BACKEND_NAME == "gpt-oss"
    assert isinstance(backend, GptOssBackend)
    assert backend.model_id == DEFAULT_GPT_OSS_MODEL_ID
    assert backend.max_new_tokens == 256
    assert backend.model is None
    assert backend.tokenizer is None


def test_factory_preserves_configured_gpt_oss_constructor_values() -> None:
    backend = build_model_backend(
        ModelBackendConfig(
            backend_name="gpt-oss",
            gpt_oss_model_id="local/gpt-oss-checkpoint",
            max_new_tokens=777,
        )
    )

    assert isinstance(backend, GptOssBackend)
    assert backend.model_id == "local/gpt-oss-checkpoint"
    assert backend.max_new_tokens == 777


def test_factory_selects_remote_gpt_oss_only_for_explicit_huggingface_mode() -> None:
    backend = build_model_backend(
        ModelBackendConfig(
            inference_mode="huggingface",
            huggingface_token=None,
            huggingface_provider="groq",
            huggingface_timeout_seconds=60,
            huggingface_max_retries=0,
        )
    )

    assert isinstance(backend, HuggingFaceGptOssBackend)
    assert backend.model_name == "openai/gpt-oss-20b"


def test_huggingface_mode_rejects_experimental_muse_backend() -> None:
    with pytest.raises(ValueError, match="requires gpt-oss"):
        ModelBackendConfig(
            backend_name="muse-glimmer",
            inference_mode="huggingface",
        )


def test_factory_selects_configured_muse_backend() -> None:
    backend = build_model_backend(
        ModelBackendConfig(
            backend_name="muse-glimmer",
            max_new_tokens=512,
            muse_base_url="http://localhost:9000/v1",
            muse_model_alias="local-muse-alias",
            muse_timeout_seconds=42,
            reasoning_strength="xhigh",
        )
    )

    assert isinstance(backend, MuseGlimmerBackend)
    assert backend.base_url == "http://localhost:9000/v1"
    assert backend.model_name == "local-muse-alias"
    assert backend.max_new_tokens == 512
    assert backend.timeout_seconds == 42.0
    assert backend.reasoning_strength == "xhigh"
    assert backend.response_grammar is None


def test_factory_forwards_optional_muse_response_grammar() -> None:
    backend = build_model_backend(
        ModelBackendConfig(
            backend_name="muse-glimmer",
            muse_response_grammar=MUSE_POLICY_DECISION_GBNF,
        )
    )

    assert isinstance(backend, MuseGlimmerBackend)
    assert backend.response_grammar == MUSE_POLICY_DECISION_GBNF


def test_factory_rejects_unsupported_backend() -> None:
    with pytest.raises(ValueError, match="backend_name"):
        ModelBackendConfig(backend_name="remote-model")
