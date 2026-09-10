from __future__ import annotations

from collections import deque

import pytest
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from openweight_platform.orchestration.model_graph import (
    build_model_routed_capability_graph,
)
from openweight_platform.orchestration.model_routing import (
    PolicyRoutingDecision,
    RoutingDecisionError,
    WebRoutingDecision,
)


class FakeModelRouter:
    def __init__(self, *decisions_or_errors) -> None:
        self._outcomes = deque(decisions_or_errors)
        self.questions: list[str] = []

    def decide(self, question: str):
        self.questions.append(question)
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def build_operations_tools(calls):
    def get_employee_record(identifier: str):
        calls.append({"identifier": identifier})
        return {"employee_id": identifier}

    def get_contractor_record(identifier: str):
        calls.append({"identifier": identifier})
        return {"contractor_id": identifier}

    def get_access_request(request_id: str):
        calls.append({"request_id": request_id})
        return {"request_id": request_id}

    return [
        StructuredTool.from_function(
            func=function,
            name=function.__name__,
            description=f"Fake {function.__name__} lookup.",
        )
        for function in (
            get_employee_record,
            get_contractor_record,
            get_access_request,
        )
    ]


def build_web_tool(calls):
    def search_web(query: str, max_results: int = 5):
        calls.append({"query": query, "max_results": max_results})
        return [{"title": "Fictional persisted web result"}]

    return StructuredTool.from_function(
        func=search_web,
        name="search_web",
        description="Fake persisted public web lookup.",
    )


def build_graph(router, *, checkpointer=None):
    calls = {
        "policy": [],
        "operations": [],
        "web": [],
    }

    def policy_retriever(question):
        calls["policy"].append(question)
        return [
            Document(
                page_content="Fictional persisted policy evidence.",
                metadata={"policy_id": "FIC-PERSIST-001"},
            )
        ]

    graph = build_model_routed_capability_graph(
        model_router=router,
        policy_retriever=policy_retriever,
        operations_tools=build_operations_tools(calls["operations"]),
        web_tool=build_web_tool(calls["web"]),
        checkpointer=checkpointer,
    )
    return graph, calls


def thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def test_graph_without_checkpointer_still_runs_without_thread_config() -> None:
    decision = PolicyRoutingDecision(route="policy")
    router = FakeModelRouter(decision)
    graph, calls = build_graph(router)

    result = graph.invoke({"question": "What does the policy require?"})

    assert isinstance(graph, CompiledStateGraph)
    assert result["routing_decision"] is decision
    assert result["route"] == "policy"
    assert router.questions == ["What does the policy require?"]
    assert calls["policy"] == ["What does the policy require?"]


def test_checkpointer_requires_official_thread_configuration() -> None:
    router = FakeModelRouter(PolicyRoutingDecision(route="policy"))
    graph, calls = build_graph(router, checkpointer=InMemorySaver())

    with pytest.raises(ValueError, match="thread_id"):
        graph.invoke({"question": "This invocation has no thread ID."})

    assert router.questions == []
    assert calls["policy"] == []
    assert calls["operations"] == []
    assert calls["web"] == []


def test_checkpoint_preserves_complete_typed_final_state_and_history() -> None:
    decision = PolicyRoutingDecision(route="policy")
    router = FakeModelRouter(decision)
    checkpointer = InMemorySaver()
    graph, calls = build_graph(router, checkpointer=checkpointer)
    config = thread_config("thread-policy-001")
    question = "What policy evidence is persisted?"

    result = graph.invoke({"question": question}, config)
    snapshot = graph.get_state(config)
    history = list(graph.get_state_history(config))

    assert result == snapshot.values
    assert snapshot.values["question"] == question
    assert snapshot.values["routing_decision"] == decision
    assert isinstance(
        snapshot.values["routing_decision"],
        PolicyRoutingDecision,
    )
    assert snapshot.values["route"] == "policy"
    assert snapshot.values["result"] == {
        "evidence": [
            {
                "content": "Fictional persisted policy evidence.",
                "metadata": {"policy_id": "FIC-PERSIST-001"},
            }
        ]
    }
    assert snapshot.next == ()
    assert len(history) >= 3
    assert history[0].values == snapshot.values
    assert router.questions == [question]
    assert calls["policy"] == [question]


def test_different_thread_ids_recover_isolated_state() -> None:
    policy_decision = PolicyRoutingDecision(route="policy")
    web_decision = WebRoutingDecision(route="web")
    router = FakeModelRouter(policy_decision, web_decision)
    graph, calls = build_graph(router, checkpointer=InMemorySaver())
    policy_config = thread_config("thread-policy")
    web_config = thread_config("thread-web")
    policy_question = "What policy applies?"
    web_question = "What current public result exists?"

    graph.invoke({"question": policy_question}, policy_config)
    graph.invoke({"question": web_question}, web_config)
    policy_state = graph.get_state(policy_config).values
    web_state = graph.get_state(web_config).values

    assert policy_state["question"] == policy_question
    assert policy_state["routing_decision"] == policy_decision
    assert policy_state["route"] == "policy"
    assert web_state["question"] == web_question
    assert web_state["routing_decision"] == web_decision
    assert web_state["route"] == "web"
    assert policy_state["result"] != web_state["result"]
    assert router.questions == [policy_question, web_question]
    assert calls["policy"] == [policy_question]
    assert calls["web"] == [
        {"query": web_question, "max_results": 5}
    ]
    assert calls["operations"] == []


def test_each_persisted_invocation_routes_and_executes_exactly_once() -> None:
    router = FakeModelRouter(
        PolicyRoutingDecision(route="policy"),
        PolicyRoutingDecision(route="policy"),
    )
    graph, calls = build_graph(router, checkpointer=InMemorySaver())
    config = thread_config("thread-repeat")

    graph.invoke({"question": "First policy question"}, config)
    graph.invoke({"question": "Second policy question"}, config)

    assert router.questions == [
        "First policy question",
        "Second policy question",
    ]
    assert calls["policy"] == [
        "First policy question",
        "Second policy question",
    ]
    assert calls["operations"] == []
    assert calls["web"] == []
    assert graph.get_state(config).values["question"] == (
        "Second policy question"
    )


def test_router_failure_on_existing_thread_remains_fail_closed() -> None:
    router = FakeModelRouter(
        PolicyRoutingDecision(route="policy"),
        RoutingDecisionError("strict routing failed"),
    )
    graph, calls = build_graph(router, checkpointer=InMemorySaver())
    config = thread_config("thread-failure")

    graph.invoke({"question": "Successful policy question"}, config)

    with pytest.raises(RoutingDecisionError, match="strict routing failed"):
        graph.invoke({"question": "Failed routing question"}, config)

    assert router.questions == [
        "Successful policy question",
        "Failed routing question",
    ]
    assert calls["policy"] == ["Successful policy question"]
    assert calls["operations"] == []
    assert calls["web"] == []
