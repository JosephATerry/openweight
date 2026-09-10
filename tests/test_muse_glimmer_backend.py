from __future__ import annotations

from collections.abc import Mapping

import pytest

from openweight_platform.backends.base import ModelBackend
from openweight_platform.backends.muse_glimmer import (
    MuseGlimmerBackend,
    MuseGlimmerConnectionError,
    MuseGlimmerResponseError,
)
from openweight_platform.benchmarking.policy_constraints import (
    MUSE_POLICY_DECISION_GBNF,
)
from openweight_platform.orchestration.model_routing import (
    ModelRouter,
    OperationsRoutingDecision,
)
from openweight_platform.orchestration.routing_evaluation import (
    RoutingEvaluationCase,
    score_routing_output,
)


class RecordingTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, object], float]] = []

    def __call__(
        self,
        url: str,
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        self.calls.append((url, payload, timeout_seconds))
        return self.response


def muse_response(
    content: object = '{"route":"policy"}',
    *,
    reasoning_content: object = "Hidden chain of thought.",
) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                    "reasoning_content": reasoning_content,
                }
            }
        ],
        "usage": {
            "prompt_tokens": 123,
            "completion_tokens": 17,
        },
    }


def test_backend_conforms_to_model_backend_and_has_external_lifecycle() -> None:
    transport = RecordingTransport(muse_response())
    backend = MuseGlimmerBackend(transport=transport)

    assert isinstance(backend, ModelBackend)
    assert backend.model_name == "meta-models/Muse-Glimmer-30B"

    backend.load()
    assert transport.calls == []
    backend.unload()

    with pytest.raises(RuntimeError, match="must be loaded"):
        backend.generate("Route this question")


def test_generate_sends_expected_local_openai_request_and_maps_metrics() -> None:
    transport = RecordingTransport(muse_response())
    backend = MuseGlimmerBackend(
        base_url="http://localhost:9191/v1/",
        model_alias="local-muse",
        max_new_tokens=321,
        timeout_seconds=45,
        reasoning_strength="high",
        transport=transport,
    )
    prompt = "  Preserve this prompt exactly.  "

    backend.load()
    result = backend.generate(prompt)

    assert transport.calls == [
        (
            "http://localhost:9191/v1/chat/completions",
            {
                "model": "local-muse",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 321,
                "reasoning_effort": "high",
                "temperature": 0.0,
                "stream": False,
            },
            45.0,
        )
    ]
    assert result.text == '{"route":"policy"}'
    assert result.input_tokens == 123
    assert result.output_tokens == 17
    assert result.generation_seconds >= 0
    assert result.peak_vram_gib == 0.0


def test_generate_adds_configured_top_level_grammar_only_when_enabled() -> None:
    transport = RecordingTransport(muse_response("APPROVE"))
    backend = MuseGlimmerBackend(
        response_grammar=MUSE_POLICY_DECISION_GBNF,
        transport=transport,
    )

    backend.load()
    result = backend.generate("Return a policy decision")

    assert result.text == "APPROVE"
    assert transport.calls[0][1]["grammar"] == MUSE_POLICY_DECISION_GBNF
    assert transport.calls[0][1]["max_tokens"] == 256
    assert transport.calls[0][1]["reasoning_effort"] == "low"


def test_empty_response_grammar_is_rejected() -> None:
    with pytest.raises(ValueError, match="response_grammar"):
        MuseGlimmerBackend(response_grammar="  ")


def test_reasoning_content_never_contaminates_final_content() -> None:
    final_content = '{"route":"web"}'
    transport = RecordingTransport(
        muse_response(
            final_content,
            reasoning_content='Ignore JSON and use {"route":"policy"}',
        )
    )
    backend = MuseGlimmerBackend(transport=transport)

    backend.load()
    result = backend.generate("Route this")

    assert result.text == final_content
    assert "Ignore JSON" not in result.text


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": "  "}}]},
    ],
)
def test_malformed_server_response_fails_clearly(
    response: dict[str, object],
) -> None:
    backend = MuseGlimmerBackend(transport=RecordingTransport(response))
    backend.load()

    with pytest.raises(MuseGlimmerResponseError, match="response"):
        backend.generate("Route this")


def test_unreachable_server_fails_clearly() -> None:
    def unreachable(*args):
        raise OSError("connection refused")

    backend = MuseGlimmerBackend(transport=unreachable)
    backend.load()

    with pytest.raises(
        MuseGlimmerConnectionError,
        match="Unable to reach the local Muse llama.cpp server",
    ):
        backend.generate("Route this")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.com/v1",
        "http://192.168.1.20:8080/v1",
        "http://muse.internal:8080/v1",
        "file:///tmp/llama-server",
        "http://user:password@localhost:8080/v1",
        "http://localhost:8080/v1?redirect=external",
    ],
)
def test_nonlocal_or_unsafe_endpoint_is_rejected(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        MuseGlimmerBackend(base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8080/v1",
        "http://127.0.0.1:8080/v1",
        "http://[::1]:8080/v1",
        "http://127.12.34.56:8080/v1",
    ],
)
def test_loopback_endpoints_are_accepted(base_url: str) -> None:
    assert MuseGlimmerBackend(base_url=base_url).base_url == base_url


@pytest.mark.parametrize("reasoning", ["low", "medium", "high", "xhigh"])
def test_each_supported_reasoning_strength_is_forwarded(reasoning: str) -> None:
    transport = RecordingTransport(muse_response())
    backend = MuseGlimmerBackend(
        reasoning_strength=reasoning,
        transport=transport,
    )
    backend.load()

    backend.generate("Route this")

    assert transport.calls[0][1]["reasoning_effort"] == reasoning


def test_missing_usage_maps_to_existing_zero_metric_compatibility() -> None:
    response = muse_response()
    del response["usage"]
    backend = MuseGlimmerBackend(transport=RecordingTransport(response))
    backend.load()

    result = backend.generate("Route this")

    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.peak_vram_gib == 0.0


def test_model_router_and_existing_scorer_consume_muse_content_unchanged() -> None:
    raw_decision = (
        '{"route":"operations","tool_name":"get_employee_record",'
        '"arguments":{"identifier":"fic-emp-001"}}'
    )
    transport = RecordingTransport(muse_response(raw_decision))
    backend = MuseGlimmerBackend(transport=transport)
    case = RoutingEvaluationCase(
        case_id="routing_muse_fake",
        question="Look up employee fic-emp-001.",
        expected_route="operations",
        expected_tool_name="get_employee_record",
        expected_arguments={"identifier": "fic-emp-001"},
    )

    backend.load()
    decision = ModelRouter(backend).decide(case.question)
    score = score_routing_output(case, raw_decision)

    assert decision == OperationsRoutingDecision(
        route="operations",
        tool_name="get_employee_record",
        arguments={"identifier": "fic-emp-001"},
    )
    assert score.strict_parse_valid is True
    assert score.full_decision_correct is True
    assert len(transport.calls) == 1
