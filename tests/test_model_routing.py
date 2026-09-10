from __future__ import annotations

import pytest

from openweight_platform.backends.base import GenerationResult, ModelBackend
from openweight_platform.orchestration.capabilities import OperationsToolCall
from openweight_platform.orchestration.model_routing import (
    ModelRouter,
    OperationsRoutingDecision,
    PolicyRoutingDecision,
    RoutingDecisionError,
    WebRoutingDecision,
    build_routing_prompt,
    parse_routing_decision,
    to_operations_tool_call,
)


class FakeBackend(ModelBackend):
    def __init__(self, generated_text: str) -> None:
        self.generated_text = generated_text
        self.prompts: list[str] = []
        self.load_calls = 0
        self.unload_calls = 0

    @property
    def model_name(self) -> str:
        return "fake-routing-model"

    def load(self) -> None:
        self.load_calls += 1

    def generate(self, prompt: str) -> GenerationResult:
        self.prompts.append(prompt)
        return GenerationResult(
            text=self.generated_text,
            input_tokens=100,
            output_tokens=12,
            generation_seconds=0.1,
            peak_vram_gib=0.0,
        )

    def unload(self) -> None:
        self.unload_calls += 1


def test_policy_json_parses_to_typed_decision() -> None:
    decision = parse_routing_decision('{"route": "policy"}')

    assert decision == PolicyRoutingDecision(route="policy")


def test_web_json_parses_to_typed_decision() -> None:
    decision = parse_routing_decision('{"route": "web"}')

    assert decision == WebRoutingDecision(route="web")


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_employee_record", {"identifier": "fic-emp-001"}),
        ("get_contractor_record", {"identifier": "Fictional Casey River"}),
        ("get_access_request", {"request_id": "fic-req-001"}),
    ],
)
def test_each_operations_tool_parses_to_typed_decision(
    tool_name,
    arguments,
) -> None:
    argument_name, argument_value = next(iter(arguments.items()))
    text = (
        '{"route": "operations", '
        f'"tool_name": "{tool_name}", '
        f'"arguments": {{"{argument_name}": "{argument_value}"}}}}'
    )

    decision = parse_routing_decision(text)

    assert isinstance(decision, OperationsRoutingDecision)
    assert decision.route == "operations"
    assert decision.tool_name == tool_name
    assert decision.arguments == arguments


@pytest.mark.parametrize(
    "text",
    [
        '{"route": "policy"',
        'The answer is {"route": "policy"}',
        '```json\n{"route": "policy"}\n```',
    ],
)
def test_malformed_or_surrounded_json_fails(text) -> None:
    with pytest.raises(RoutingDecisionError, match="valid JSON object"):
        parse_routing_decision(text)


def test_unsupported_route_fails() -> None:
    with pytest.raises(RoutingDecisionError, match="decision schema"):
        parse_routing_decision('{"route": "finance"}')


def test_unsupported_operations_tool_fails() -> None:
    with pytest.raises(RoutingDecisionError, match="decision schema"):
        parse_routing_decision(
            '{"route": "operations", "tool_name": "delete_employee", '
            '"arguments": {"identifier": "fic-emp-001"}}'
        )


def test_operations_route_without_tool_selection_fails() -> None:
    with pytest.raises(RoutingDecisionError, match="decision schema"):
        parse_routing_decision('{"route": "operations", "arguments": {}}')


@pytest.mark.parametrize("route", ["policy", "web"])
def test_non_operations_routes_reject_operations_fields(route) -> None:
    with pytest.raises(RoutingDecisionError, match="decision schema"):
        parse_routing_decision(
            f'{{"route": "{route}", "tool_name": "get_employee_record", '
            '"arguments": {"identifier": "fic-emp-001"}}'
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"route": "operations", "tool_name": "get_employee_record", '
        '"arguments": {"request_id": "fic-req-001"}}',
        '{"route": "operations", "tool_name": "get_access_request", '
        '"arguments": {"identifier": "fic-emp-001"}}',
        '{"route": "operations", "tool_name": "get_contractor_record", '
        '"arguments": {"identifier": ""}}',
        '{"route": "operations", "tool_name": "get_employee_record", '
        '"arguments": {"identifier": "fic-emp-001", "extra": true}}',
    ],
)
def test_operations_argument_shape_is_strict(text) -> None:
    with pytest.raises(RoutingDecisionError, match="decision schema"):
        parse_routing_decision(text)


def test_prompt_defines_routes_tools_and_json_only_output() -> None:
    prompt = build_routing_prompt("Which capability should handle this?")

    assert "POLICY:" in prompt
    assert "OPERATIONS:" in prompt
    assert "WEB:" in prompt
    assert "get_employee_record" in prompt
    assert "get_contractor_record" in prompt
    assert "get_access_request" in prompt
    assert "Return JSON only" in prompt
    assert "Do not include an explanation, Markdown, or code fences" in prompt


def test_prompt_includes_original_question_faithfully() -> None:
    question = 'Who is assigned to request "fic-req-001"?'

    prompt = build_routing_prompt(question)

    assert '"Who is assigned to request \\"fic-req-001\\"?"' in prompt


@pytest.mark.parametrize("question", ["", "   ", None])
def test_prompt_rejects_empty_or_non_string_question(question) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        build_routing_prompt(question)


def test_model_router_sends_expected_prompt_and_returns_typed_decision() -> None:
    backend = FakeBackend(
        '{"route": "operations", "tool_name": "get_employee_record", '
        '"arguments": {"identifier": "fic-emp-001"}}'
    )
    router = ModelRouter(backend)
    question = "Find fictional employee fic-emp-001."

    decision = router.decide(question)

    assert backend.prompts == [build_routing_prompt(question)]
    assert decision == OperationsRoutingDecision(
        route="operations",
        tool_name="get_employee_record",
        arguments={"identifier": "fic-emp-001"},
    )
    assert backend.load_calls == 0
    assert backend.unload_calls == 0


def test_model_router_callable_returns_typed_decision() -> None:
    router = ModelRouter(FakeBackend('{"route": "web"}'))

    assert router("What changed today?") == WebRoutingDecision(route="web")


def test_operations_decision_converts_to_existing_tool_call() -> None:
    decision = OperationsRoutingDecision(
        route="operations",
        tool_name="get_access_request",
        arguments={"request_id": "fic-req-001"},
    )

    tool_call = to_operations_tool_call(decision)

    assert isinstance(tool_call, OperationsToolCall)
    assert tool_call == OperationsToolCall(
        tool_name="get_access_request",
        arguments={"request_id": "fic-req-001"},
    )
    assert tool_call.arguments is not decision.arguments


def test_non_operations_decision_cannot_become_tool_call() -> None:
    with pytest.raises(ValueError, match="operations routing decision"):
        to_operations_tool_call(PolicyRoutingDecision(route="policy"))
