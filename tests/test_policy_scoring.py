from openweight_platform.benchmarking.datasets import (
    PolicyEvaluationCase,
)
from openweight_platform.benchmarking.runner import (
    BenchmarkRecord,
)
from openweight_platform.benchmarking.scoring import (
    parse_decision,
    score_policy_record,
    summarize_scores,
)


def make_case(
    expected_decision: str = "APPROVE",
) -> PolicyEvaluationCase:
    return PolicyEvaluationCase(
        case_id="policy_test",
        category="test",
        policy="Example policy",
        request="Example request",
        expected_decision=expected_decision,
    )


def make_record(
    response: str,
) -> BenchmarkRecord:
    return BenchmarkRecord(
        case_id="policy_test",
        model_name="fake-model",
        prompt="Example prompt",
        response=response,
        input_tokens=10,
        output_tokens=2,
        generation_seconds=1.0,
        tokens_per_second=2.0,
        peak_vram_gib=1.0,
    )


def test_parse_valid_decision():
    assert parse_decision("APPROVE") == "APPROVE"
    assert parse_decision(" deny ") == "DENY"
    assert parse_decision("needs_info") == "NEEDS_INFO"


def test_parse_rejects_extra_text():
    assert parse_decision(
        "The correct decision is APPROVE."
    ) is None


def test_correct_decision_scores_true():
    score = score_policy_record(
        make_case("APPROVE"),
        make_record("APPROVE"),
    )

    assert score.format_valid is True
    assert score.correct is True
    assert score.predicted_decision == "APPROVE"


def test_wrong_decision_scores_false():
    score = score_policy_record(
        make_case("DENY"),
        make_record("APPROVE"),
    )

    assert score.format_valid is True
    assert score.correct is False


def test_invalid_format_scores_false():
    score = score_policy_record(
        make_case("APPROVE"),
        make_record("I would approve this request."),
    )

    assert score.format_valid is False
    assert score.correct is False
    assert score.predicted_decision is None


def test_summary_metrics():
    scores = [
        score_policy_record(
            make_case("APPROVE"),
            make_record("APPROVE"),
        ),
        score_policy_record(
            make_case("DENY"),
            make_record("APPROVE"),
        ),
        score_policy_record(
            make_case("APPROVE"),
            make_record("I would approve this."),
        ),
    ]

    summary = summarize_scores(scores)

    assert summary["total_cases"] == 3
    assert summary["correct_cases"] == 1
    assert summary["format_valid_cases"] == 2
    assert summary["accuracy"] == 1 / 3
    assert summary["format_compliance"] == 2 / 3
