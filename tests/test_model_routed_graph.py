from __future__ import annotations

from typing import get_args, get_type_hints

import pytest
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from langgraph.graph.state import CompiledStateGraph

import openweight_platform.orchestration.model_graph as model_graph
from openweight_platform.orchestration.capabilities import OperationsToolCall
from openweight_platform.orchestration.model_graph import (
    ModelRoutedInput,
    ModelRoutedOutput,
    ModelRoutedState,
    build_model_routed_capability_graph,
)
from openweight_platform.orchestration.model_routing import (
    OperationsRoutingDecision,
    PolicyRoutingDecision,
    RoutingDecisionError,
    WebRoutingDecision,
)


class FakeModelRouter:
    def __init__(self, decision=None, error=None) -> None:
        self.decision = decision
        self.error = error
        self.questions: list[str] = []

    def decide(self, question: str):
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        return self.decision


def empty_operations_calls():
    return {
        "get_employee_record": [],
        "get_contractor_record": [],
        "get_access_request": [],
    }


def build_operations_tools(calls):
    def get_employee_record(identifier: str):
        calls["get_employee_record"].append({"identifier": identifier})
        return {"kind": "employee", "identifier": identifier}

    def get_contractor_record(identifier: str):
        calls["get_contractor_record"].append({"identifier": identifier})
        return {"kind": "contractor", "identifier": identifier}

    def get_access_request(request_id: str):
        calls["get_access_request"].append({"request_id": request_id})
        return {"kind": "access_request", "request_id": request_id}

    functions = (
        get_employee_record,
        get_contractor_record,
        get_access_request,
    )
    return [
        StructuredTool.from_function(
            func=function,
            name=function.__name__,
            description=f"Fake {function.__name__} capability.",
        )
        for function in functions
    ]


def build_web_tool(calls):
    def search_web(query: str, max_results: int = 5):
        calls.append({"query": query, "max_results": max_results})
        return [{"title": "Fictional public result", "query": query}]

    return StructuredTool.from_function(
        func=search_web,
        name="search_web",
        description="Fake current public web capability.",
    )


def build_graph(decision=None, error=None):
    router = FakeModelRouter(decision=decision, error=error)
    calls = {
        "policy": [],
        "web": [],
        "operations": empty_operations_calls(),
    }

    def policy_retriever(question):
        calls["policy"].append(question)
        return [
            Document(
                page_content="Fictional internal policy evidence.",
                metadata={"policy_id": "FIC-POLICY-001"},
            )
        ]

    graph = build_model_routed_capability_graph(
        model_router=router,
        policy_retriever=policy_retriever,
        operations_tools=build_operations_tools(calls["operations"]),
        web_tool=build_web_tool(calls["web"]),
    )
    return graph, router, calls


def assert_no_capability_calls(calls) -> None:
    assert calls["policy"] == []
    assert calls["web"] == []
    assert all(tool_calls == [] for tool_calls in calls["operations"].values())


def test_state_schema_requires_only_question_as_input() -> None:
    assert ModelRoutedInput.__required_keys__ == frozenset({"question"})
    assert ModelRoutedState.__required_keys__ == frozenset({"question"})
    assert ModelRoutedState.__optional_keys__ == frozenset(
        {"routing_decision", "route", "result"}
    )
    assert ModelRoutedOutput.__required_keys__ == frozenset(
        {"question", "routing_decision", "route", "result"}
    )
    assert set(get_args(get_type_hints(ModelRoutedState)["route"])) == {
        "policy",
        "operations",
        "web",
    }


@pytest.mark.parametrize(
    ("decision", "selected_capability"),
    [
        (PolicyRoutingDecision(route="policy"), "policy"),
        (WebRoutingDecision(route="web"), "web"),
    ],
)
def test_policy_and_web_decisions_execute_only_selected_capability(
    decision,
    selected_capability,
) -> None:
    question = "Use exactly the model-selected fictional capability."
    graph, router, calls = build_graph(decision=decision)

    result = graph.invoke({"question": question})

    assert isinstance(graph, CompiledStateGraph)
    assert router.questions == [question]
    assert result["question"] == question
    assert result["routing_decision"] is decision
    assert result["route"] == selected_capability
    assert calls[selected_capability] != []
    expected_results = {
        "policy": {
            "evidence": [
                {
                    "content": "Fictional internal policy evidence.",
                    "metadata": {"policy_id": "FIC-POLICY-001"},
                }
            ]
        },
        "web": {
            "results": [
                {
                    "title": "Fictional public result",
                    "query": question,
                }
            ]
        },
    }
    assert result["result"] == expected_results[selected_capability]
    assert calls["policy"] == (
        [question] if selected_capability == "policy" else []
    )
    assert calls["web"] == (
        [{"query": question, "max_results": 5}]
        if selected_capability == "web"
        else []
    )
    assert all(
        tool_calls == []
        for tool_calls in calls["operations"].values()
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_output"),
    [
        (
            "get_employee_record",
            {"identifier": "Avery Example"},
            {"kind": "employee", "identifier": "Avery Example"},
        ),
        (
            "get_contractor_record",
            {"identifier": "fic-ctr-001"},
            {"kind": "contractor", "identifier": "fic-ctr-001"},
        ),
        (
            "get_access_request",
            {"request_id": "fic-req-002"},
            {"kind": "access_request", "request_id": "fic-req-002"},
        ),
    ],
)
def test_operations_decision_executes_exactly_encoded_tool_and_arguments(
    tool_name,
    arguments,
    expected_output,
) -> None:
    decision = OperationsRoutingDecision(
        route="operations",
        tool_name=tool_name,
        arguments=arguments,
    )
    graph, router, calls = build_graph(decision=decision)
    question = "Use the validated operations selection."

    result = graph.invoke({"question": question})

    assert router.questions == [question]
    assert result == {
        "question": question,
        "routing_decision": decision,
        "route": "operations",
        "result": {
            "tool_name": tool_name,
            "arguments": arguments,
            "output": expected_output,
        },
    }
    assert calls["operations"][tool_name] == [arguments]
    assert all(
        tool_calls == []
        for name, tool_calls in calls["operations"].items()
        if name != tool_name
    )
    assert calls["policy"] == []
    assert calls["web"] == []


def test_operations_uses_same_decision_object_and_existing_conversion(
    monkeypatch,
) -> None:
    decision = OperationsRoutingDecision(
        route="operations",
        tool_name="get_employee_record",
        arguments={"identifier": "fic-emp-001"},
    )
    converted_decisions = []
    original_converter = model_graph.to_operations_tool_call

    def recording_converter(received_decision):
        converted_decisions.append(received_decision)
        return original_converter(received_decision)

    monkeypatch.setattr(
        model_graph,
        "to_operations_tool_call",
        recording_converter,
    )
    graph, router, _ = build_graph(decision=decision)

    graph.invoke({"question": "Find fictional employee fic-emp-001."})

    assert router.questions == ["Find fictional employee fic-emp-001."]
    assert converted_decisions == [decision]
    assert converted_decisions[0] is decision


def test_router_failure_executes_no_capability_and_has_no_fallback() -> None:
    error = RoutingDecisionError("strict routing output was invalid")
    graph, router, calls = build_graph(error=error)
    question = "Latest internal employee policy web record"

    with pytest.raises(RoutingDecisionError, match="strict routing output"):
        graph.invoke({"question": question})

    assert router.questions == [question]
    assert_no_capability_calls(calls)


def test_inconsistent_validated_decision_fails_before_capability() -> None:
    inconsistent = PolicyRoutingDecision.model_construct(route="web")
    graph, router, calls = build_graph(decision=inconsistent)

    with pytest.raises(ValueError, match="Inconsistent routing decision"):
        graph.invoke({"question": "This must fail closed."})

    assert router.questions == ["This must fail closed."]
    assert_no_capability_calls(calls)


def test_unvalidated_router_value_fails_before_capability() -> None:
    graph, router, calls = build_graph(decision={"route": "policy"})

    with pytest.raises(TypeError, match="validated RoutingDecision"):
        graph.invoke({"question": "Do not coerce this dictionary."})

    assert router.questions == ["Do not coerce this dictionary."]
    assert_no_capability_calls(calls)


def test_question_keywords_do_not_override_model_decision() -> None:
    decision = PolicyRoutingDecision(route="policy")
    graph, router, calls = build_graph(decision=decision)
    question = "What is the latest current public web result?"

    result = graph.invoke({"question": question})

    assert result["route"] == "policy"
    assert router.questions == [question]
    assert calls["policy"] == [question]
    assert calls["web"] == []
    assert all(
        tool_calls == []
        for tool_calls in calls["operations"].values()
    )
