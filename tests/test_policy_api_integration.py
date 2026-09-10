from __future__ import annotations

import pytest
from langchain_core.documents import Document

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.errors import UnsafeResultError
from openweight_platform.api.observability import create_observability
from openweight_platform.api.runtime import DefaultPlatformRuntime
from openweight_platform.backends.base import GenerationResult, ModelBackend
from openweight_platform.rag.generation import INSUFFICIENT_EVIDENCE_SENTINEL


class FakePolicyStore:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, int]] = []

    def similarity_search(self, query: str, k: int) -> list[Document]:
        self.calls.append((query, k))
        return self.documents[:k]


class FakePolicyBackend(ModelBackend):
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return "model-free-policy-fixture"

    def load(self) -> None:
        return None

    def generate(self, prompt: str) -> GenerationResult:
        self.prompts.append(prompt)
        return GenerationResult(
            text=self.answer,
            input_tokens=10,
            output_tokens=8,
            generation_seconds=0.01,
            peak_vram_gib=0.0,
        )

    def unload(self) -> None:
        return None


def policy_document() -> Document:
    return Document(
        page_content="Privileged access requires explicit approval.",
        metadata={
            "policy_id": "EPG-ACCESS-001",
            "title": "Privileged Infrastructure Access Policy",
            "domain": "infrastructure-access",
            "source_path": "data/policies/01_privileged.md",
            "chunk_index": 1,
        },
    )


def runtime_with(
    store: FakePolicyStore,
    backend: FakePolicyBackend,
) -> DefaultPlatformRuntime:
    settings = ServiceSettings.from_env(
        {
            "OPENWEIGHT_DATABASE_REQUIRED": "false",
            "OPENWEIGHT_OBSERVABILITY_ENABLED": "false",
            "OPENWEIGHT_ENVIRONMENT": "test",
        }
    )
    runtime = DefaultPlatformRuntime(
        settings,
        observability=create_observability(settings),
    )
    runtime._policy = store  # type: ignore[assignment]
    runtime._backend = backend
    runtime._backend_loaded = True
    return runtime


def test_runtime_reuses_grounded_generator_and_maps_employee_safe_evidence() -> None:
    store = FakePolicyStore([policy_document()])
    backend = FakePolicyBackend(
        "Privileged access requires approval [EPG-ACCESS-001#1]."
    )
    runtime = runtime_with(store, backend)

    result = runtime._query_policy(
        "What controls privileged access?",
        request_id="policy-integration-1",
    )

    assert store.calls == [("What controls privileged access?", 3)]
    assert len(backend.prompts) == 1
    assert policy_document().page_content in backend.prompts[0]
    assert result.status == "answered"
    assert result.citations == ["[EPG-ACCESS-001#1]"]
    assert result.citation_valid is True
    assert result.evidence[0].policy_id == "EPG-ACCESS-001"
    assert result.evidence[0].content == policy_document().page_content
    assert not hasattr(result.evidence[0], "source_path")


def test_runtime_fails_closed_when_generated_citation_was_not_retrieved() -> None:
    runtime = runtime_with(
        FakePolicyStore([policy_document()]),
        FakePolicyBackend("Use this control [EPG-FABRICATED-999#0]."),
    )

    with pytest.raises(UnsafeResultError):
        runtime._query_policy(
            "What controls privileged access?",
            request_id="policy-integration-2",
        )


def test_runtime_returns_insufficient_evidence_without_generation() -> None:
    store = FakePolicyStore([])
    backend = FakePolicyBackend("This should not be generated.")
    runtime = runtime_with(store, backend)

    result = runtime._query_policy(
        "What unsupported rule applies?",
        request_id="policy-integration-3",
    )

    assert result.status == "insufficient_evidence"
    assert result.citations == []
    assert result.evidence == []
    assert backend.prompts == []


def test_runtime_maps_model_assessed_insufficiency_without_exposing_weak_sources() -> None:
    store = FakePolicyStore([policy_document()])
    backend = FakePolicyBackend(INSUFFICIENT_EVIDENCE_SENTINEL)
    runtime = runtime_with(store, backend)

    result = runtime._query_policy(
        "What unrelated rule applies?",
        request_id="policy-integration-4",
    )

    assert len(backend.prompts) == 1
    assert result.status == "insufficient_evidence"
    assert result.citations == []
    assert result.evidence == []
