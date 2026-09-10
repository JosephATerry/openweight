import json
from datetime import datetime, timezone

import pytest

from openweight_platform.backends.base import GenerationResult, ModelBackend
from openweight_platform.benchmarking.policy_baseline import (
    build_result_document,
    format_report,
    run_policy_baseline,
)
from openweight_platform.benchmarking.scoring import summarize_scores


class _SensitiveGenerationError(RuntimeError):
    pass


class FailingPolicyBackend(ModelBackend):
    def __init__(self) -> None:
        self.max_new_tokens = 256
        self.load_calls = 0
        self.unload_calls = 0
        self.generate_calls: list[str] = []
        self.observed_token_limits: list[int] = []

    @property
    def model_name(self) -> str:
        return "fake-policy-model"

    def load(self) -> None:
        self.load_calls += 1

    def generate(self, prompt: str) -> GenerationResult:
        self.generate_calls.append(prompt)
        self.observed_token_limits.append(self.max_new_tokens)
        call_number = len(self.generate_calls)

        if call_number == 2:
            raise _SensitiveGenerationError(
                "reasoning_content=hidden; API_KEY=do-not-persist"
            )

        response = "APPROVE" if call_number == 1 else "NEEDS_INFO"
        return GenerationResult(
            text=response,
            input_tokens=20,
            output_tokens=2,
            generation_seconds=0.5,
            peak_vram_gib=1.25,
        )

    def unload(self) -> None:
        self.unload_calls += 1


@pytest.fixture
def policy_dataset(tmp_path):
    path = tmp_path / "policy_cases.jsonl"
    cases = [
        {
            "case_id": "policy_success_before",
            "category": "approval",
            "policy": "Approved requests may proceed.",
            "request": "This request is approved.",
            "expected_decision": "APPROVE",
        },
        {
            "case_id": "policy_generation_failure",
            "category": "denial",
            "policy": "Denied requests must not proceed.",
            "request": "This request is denied.",
            "expected_decision": "DENY",
        },
        {
            "case_id": "policy_success_after",
            "category": "missing_information",
            "policy": "Incomplete requests require more information.",
            "request": "This request is incomplete.",
            "expected_decision": "NEEDS_INFO",
        },
    ]
    path.write_text(
        "".join(json.dumps(case) + "\n" for case in cases),
        encoding="utf-8",
    )
    return path


def test_generation_failure_is_scored_and_remaining_cases_run(
    policy_dataset,
):
    backend = FailingPolicyBackend()

    scores = run_policy_baseline(backend, policy_dataset)

    assert [score.case_id for score in scores] == [
        "policy_success_before",
        "policy_generation_failure",
        "policy_success_after",
    ]
    assert len(backend.generate_calls) == 3
    assert backend.load_calls == 1
    assert backend.unload_calls == 1
    assert backend.observed_token_limits == [256, 256, 256]
    assert backend.max_new_tokens == 256

    failed = scores[1]
    assert failed.predicted_decision is None
    assert failed.format_valid is False
    assert failed.correct is False
    assert failed.response == ""
    assert failed.generation_seconds >= 0
    assert failed.tokens_per_second is None
    assert failed.peak_vram_gib is None
    assert failed.generation_error_type == "_SensitiveGenerationError"
    assert failed.generation_error_message == (
        "Backend generation failed before producing a usable response."
    )

    assert scores[0].correct is True
    assert scores[2].correct is True
    assert summarize_scores(scores) == {
        "total_cases": 3,
        "correct_cases": 2,
        "format_valid_cases": 2,
        "accuracy": 2 / 3,
        "format_compliance": 2 / 3,
    }


def test_failure_document_is_sanitized_and_success_schema_is_unchanged(
    policy_dataset,
):
    scores = run_policy_baseline(FailingPolicyBackend(), policy_dataset)

    document = build_result_document(
        model_name="fake-policy-model",
        dataset=policy_dataset,
        scores=scores,
        timestamp=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    serialized = json.dumps(document)

    assert "reasoning_content" not in serialized
    assert "do-not-persist" not in serialized
    assert "API_KEY" not in serialized
    assert "generation_error" not in document["cases"][0]
    assert document["cases"][0] == {
        "case_id": "policy_success_before",
        "category": "approval",
        "expected_decision": "APPROVE",
        "predicted_decision": "APPROVE",
        "format_valid": True,
        "correct": True,
        "generation_seconds": 0.5,
        "tokens_per_second": 4.0,
        "peak_vram_gib": 1.25,
    }

    failed = document["cases"][1]
    assert failed["format_valid"] is False
    assert failed["correct"] is False
    assert failed["tokens_per_second"] is None
    assert failed["peak_vram_gib"] is None
    assert failed["generation_error"] == {
        "type": "_SensitiveGenerationError",
        "message": (
            "Backend generation failed before producing a usable response."
        ),
    }

    report = format_report(scores)
    assert "Tokens per second: unavailable" in report
    assert "Peak VRAM: unavailable" in report
    assert "reasoning_content" not in report
    assert "do-not-persist" not in report
