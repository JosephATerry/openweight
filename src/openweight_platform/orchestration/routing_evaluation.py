"""Deterministic dataset loading and scoring for routing decisions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from openweight_platform.orchestration.model_routing import (
    OperationsRoutingDecision,
    OperationsToolName,
    PolicyRoutingDecision,
    RoutingDecision,
    RoutingDecisionError,
    WebRoutingDecision,
    parse_routing_decision,
)
from openweight_platform.orchestration.routing import Route, SUPPORTED_ROUTES


_BASE_CASE_FIELDS = {"case_id", "question", "expected_route"}
_OPERATIONS_CASE_FIELDS = _BASE_CASE_FIELDS | {
    "expected_tool_name",
    "expected_arguments",
}


@dataclass(frozen=True)
class RoutingEvaluationCase:
    case_id: str
    question: str
    expected_route: Route
    expected_tool_name: OperationsToolName | None = None
    expected_arguments: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be a non-empty string")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question must be a non-empty string")
        if self.expected_route not in SUPPORTED_ROUTES:
            raise ValueError(f"Unsupported expected route: {self.expected_route!r}")

        if self.expected_route == "operations":
            if self.expected_tool_name is None or self.expected_arguments is None:
                raise ValueError(
                    "Operations cases require expected tool and arguments"
                )
            try:
                OperationsRoutingDecision(
                    route="operations",
                    tool_name=self.expected_tool_name,
                    arguments=self.expected_arguments,
                )
            except ValidationError as error:
                raise ValueError(
                    "Operations expectations do not match the routing schema"
                ) from error
            object.__setattr__(
                self,
                "expected_arguments",
                dict(self.expected_arguments),
            )
        elif (
            self.expected_tool_name is not None
            or self.expected_arguments is not None
        ):
            raise ValueError(
                "Policy and web cases cannot contain operations expectations"
            )


@dataclass(frozen=True)
class ScoredRoutingDecision:
    case_id: str
    expected_route: Route
    predicted_route: Route | None
    predicted_tool_name: OperationsToolName | None
    predicted_arguments: dict[str, object] | None
    strict_parse_valid: bool
    route_correct: bool
    operations_tool_correct: bool | None
    operations_arguments_correct: bool | None
    full_decision_correct: bool
    model_output: str


def load_routing_evaluation_cases(
    path: str | Path,
) -> list[RoutingEvaluationCase]:
    """Load and validate fixed routing cases from a JSON Lines file."""
    cases = []
    seen_case_ids = set()

    with Path(path).open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue

            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError(
                    f"Routing case on line {line_number} must be an object"
                )

            expected_route = data.get("expected_route")
            expected_fields = (
                _OPERATIONS_CASE_FIELDS
                if expected_route == "operations"
                else _BASE_CASE_FIELDS
            )
            if set(data) != expected_fields:
                raise ValueError(
                    f"Routing case fields are invalid on line {line_number}"
                )

            case = RoutingEvaluationCase(
                case_id=data["case_id"],
                question=data["question"],
                expected_route=cast(Route, expected_route),
                expected_tool_name=cast(
                    OperationsToolName | None,
                    data.get("expected_tool_name"),
                ),
                expected_arguments=data.get("expected_arguments"),
            )

            if case.case_id in seen_case_ids:
                raise ValueError(
                    f"Duplicate routing case ID on line {line_number}: "
                    f"{case.case_id}"
                )

            seen_case_ids.add(case.case_id)
            cases.append(case)

    return cases


def score_routing_output(
    case: RoutingEvaluationCase,
    model_output: str,
) -> ScoredRoutingDecision:
    """Strictly parse and score one model output without repairing it."""
    try:
        decision = parse_routing_decision(model_output)
    except RoutingDecisionError:
        decision = None

    strict_parse_valid = decision is not None
    predicted_route = decision.route if decision is not None else None
    route_correct = predicted_route == case.expected_route

    if isinstance(decision, OperationsRoutingDecision):
        predicted_tool_name = decision.tool_name
        predicted_arguments = dict(decision.arguments)
    else:
        predicted_tool_name = None
        predicted_arguments = None

    if case.expected_route == "operations":
        operations_tool_correct = (
            predicted_tool_name == case.expected_tool_name
        )
        operations_arguments_correct = (
            predicted_arguments == case.expected_arguments
        )
    else:
        operations_tool_correct = None
        operations_arguments_correct = None

    full_decision_correct = (
        decision is not None
        and decision == _expected_decision(case)
    )

    return ScoredRoutingDecision(
        case_id=case.case_id,
        expected_route=case.expected_route,
        predicted_route=predicted_route,
        predicted_tool_name=predicted_tool_name,
        predicted_arguments=predicted_arguments,
        strict_parse_valid=strict_parse_valid,
        route_correct=route_correct,
        operations_tool_correct=operations_tool_correct,
        operations_arguments_correct=operations_arguments_correct,
        full_decision_correct=full_decision_correct,
        model_output=model_output,
    )


def summarize_routing_scores(
    scores: Sequence[ScoredRoutingDecision],
) -> dict[str, int | float]:
    """Aggregate strict-format, route, tool, argument, and full accuracy."""
    operations_scores = [
        score
        for score in scores
        if score.expected_route == "operations"
    ]

    return {
        "total_cases": len(scores),
        "strict_format_rate": _rate(
            [score.strict_parse_valid for score in scores]
        ),
        "overall_route_accuracy": _rate(
            [score.route_correct for score in scores]
        ),
        "policy_route_accuracy": _route_accuracy(scores, "policy"),
        "operations_route_accuracy": _route_accuracy(scores, "operations"),
        "web_route_accuracy": _route_accuracy(scores, "web"),
        "operations_tool_selection_accuracy": _rate(
            [
                score.operations_tool_correct is True
                for score in operations_scores
            ]
        ),
        "operations_argument_accuracy": _rate(
            [
                score.operations_arguments_correct is True
                for score in operations_scores
            ]
        ),
        "full_decision_accuracy": _rate(
            [score.full_decision_correct for score in scores]
        ),
    }


def _expected_decision(case: RoutingEvaluationCase) -> RoutingDecision:
    if case.expected_route == "policy":
        return PolicyRoutingDecision(route="policy")
    if case.expected_route == "web":
        return WebRoutingDecision(route="web")

    assert case.expected_tool_name is not None
    assert case.expected_arguments is not None
    return OperationsRoutingDecision(
        route="operations",
        tool_name=case.expected_tool_name,
        arguments=case.expected_arguments,
    )


def _route_accuracy(
    scores: Sequence[ScoredRoutingDecision],
    route: Route,
) -> float:
    route_scores = [
        score.route_correct
        for score in scores
        if score.expected_route == route
    ]
    return _rate(route_scores)


def _rate(values: Sequence[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


__all__ = [
    "RoutingEvaluationCase",
    "ScoredRoutingDecision",
    "load_routing_evaluation_cases",
    "score_routing_output",
    "summarize_routing_scores",
]
