"""Strict model-generated routing decisions for platform capabilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, model_validator

from openweight_platform.backends.base import ModelBackend
from openweight_platform.orchestration.capabilities import OperationsToolCall


OperationsToolName: TypeAlias = Literal[
    "get_employee_record",
    "get_contractor_record",
    "get_access_request",
]


class _RoutingDecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PolicyRoutingDecision(_RoutingDecisionModel):
    route: Literal["policy"]


class WebRoutingDecision(_RoutingDecisionModel):
    route: Literal["web"]


class OperationsRoutingDecision(_RoutingDecisionModel):
    route: Literal["operations"]
    tool_name: OperationsToolName
    arguments: dict[str, object]

    @model_validator(mode="after")
    def validate_tool_arguments(self) -> "OperationsRoutingDecision":
        expected_argument = (
            "request_id"
            if self.tool_name == "get_access_request"
            else "identifier"
        )
        expected_keys = {expected_argument}

        if set(self.arguments) != expected_keys:
            raise ValueError(
                f"{self.tool_name} arguments must contain exactly "
                f"{expected_argument!r}"
            )

        value = self.arguments[expected_argument]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{expected_argument} must be a non-empty string"
            )

        return self


RoutingDecision: TypeAlias = (
    PolicyRoutingDecision
    | OperationsRoutingDecision
    | WebRoutingDecision
)

_ROUTING_DECISION_ADAPTER = TypeAdapter(RoutingDecision)


class RoutingDecisionError(ValueError):
    """Raised when model output is not one valid routing decision."""


def parse_routing_decision(text: str) -> RoutingDecision:
    """Parse one complete JSON object into a validated routing decision."""
    if not isinstance(text, str):
        raise RoutingDecisionError("Routing output must be a JSON string")

    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise RoutingDecisionError(
            "Routing output must be exactly one valid JSON object"
        ) from error

    if not isinstance(value, dict):
        raise RoutingDecisionError("Routing output must be a JSON object")

    try:
        return _ROUTING_DECISION_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise RoutingDecisionError(
            "Routing JSON does not match the required decision schema"
        ) from error


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def build_routing_prompt(question: str) -> str:
    """Build the deterministic capability-selection prompt."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    encoded_question = json.dumps(question, ensure_ascii=False)
    return f"""Select exactly one capability for the user's question.

Route definitions:
- POLICY: Use for questions about the organization's internal policies,
  requirements, controls, procedures, or rules.
- OPERATIONS: Use for questions requiring current internal employee,
  contractor, or access-request records.
- WEB: Use for current, external, or public information that may have changed
  and requires live web search.

Available OPERATIONS tools and exact argument shapes:
- get_employee_record: {{"identifier": "<exact employee ID or full name>"}}
- get_contractor_record: {{"identifier": "<exact contractor ID or full name>"}}
- get_access_request: {{"request_id": "<exact access-request ID>"}}

Return exactly one of these JSON object shapes:
{{"route": "policy"}}
{{"route": "web"}}
{{"route": "operations", "tool_name": "<allowed tool name>", "arguments": {{...}}}}

Return JSON only. Do not include an explanation, Markdown, or code fences.
Do not answer the question and do not invent missing operation arguments.

User question as a JSON string:
{encoded_question}"""


@dataclass(frozen=True)
class ModelRouter:
    """Use an injected backend to produce and validate one routing decision."""

    backend: ModelBackend

    def decide(self, question: str) -> RoutingDecision:
        prompt = build_routing_prompt(question)
        generation = self.backend.generate(prompt)
        return parse_routing_decision(generation.text)

    def __call__(self, question: str) -> RoutingDecision:
        return self.decide(question)


def to_operations_tool_call(
    decision: RoutingDecision,
) -> OperationsToolCall:
    """Convert a validated operations decision to the existing tool contract."""
    if not isinstance(decision, OperationsRoutingDecision):
        raise ValueError("Only an operations routing decision has a tool call")

    return OperationsToolCall(
        tool_name=decision.tool_name,
        arguments=dict(decision.arguments),
    )


__all__ = [
    "ModelRouter",
    "OperationsRoutingDecision",
    "OperationsToolName",
    "PolicyRoutingDecision",
    "RoutingDecision",
    "RoutingDecisionError",
    "WebRoutingDecision",
    "build_routing_prompt",
    "parse_routing_decision",
    "to_operations_tool_call",
]
