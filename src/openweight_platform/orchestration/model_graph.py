"""LangGraph composition driven by one validated model routing decision."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypedDict, cast

from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from openweight_platform.orchestration.capabilities import (
    OperationsCapabilityAdapter,
    PolicyCapabilityAdapter,
    PolicyRetriever,
    WebCapabilityAdapter,
)
from openweight_platform.orchestration.model_routing import (
    OperationsRoutingDecision,
    PolicyRoutingDecision,
    RoutingDecision,
    WebRoutingDecision,
    to_operations_tool_call,
)
from openweight_platform.orchestration.routing import Route


class ModelDecisionRouter(Protocol):
    def decide(self, question: str) -> RoutingDecision: ...


class ModelRoutedInput(TypedDict):
    question: str


class ModelRoutedState(ModelRoutedInput, total=False):
    routing_decision: RoutingDecision
    route: Route
    result: object


class ModelRoutedOutput(TypedDict):
    question: str
    routing_decision: RoutingDecision
    route: Route
    result: object


def build_model_routed_capability_graph(
    model_router: ModelDecisionRouter,
    policy_retriever: PolicyRetriever,
    operations_tools: Sequence[BaseTool],
    web_tool: BaseTool,
    *,
    web_max_results: int = 5,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Build capability routing controlled by one injected model decision."""
    policy_handler = PolicyCapabilityAdapter(policy_retriever)
    web_handler = WebCapabilityAdapter(web_tool, max_results=web_max_results)

    def model_route(state: ModelRoutedState) -> dict[str, object]:
        question = _validated_question(state)
        decision = _validated_model_decision(model_router.decide(question))
        return {
            "routing_decision": decision,
            "route": decision.route,
        }

    def selected_route(state: ModelRoutedState) -> Route:
        _, decision = _validated_routed_state(state)
        return decision.route

    def run_policy(state: ModelRoutedState) -> dict[str, object]:
        question, _ = _validated_routed_state(state, expected_route="policy")
        return {"result": policy_handler(question)}

    def run_operations(state: ModelRoutedState) -> dict[str, object]:
        question, decision = _validated_routed_state(
            state,
            expected_route="operations",
        )
        tool_call = to_operations_tool_call(decision)
        operations_handler = OperationsCapabilityAdapter(
            operations_tools,
            lambda received_question: tool_call,
        )
        return {"result": operations_handler(question)}

    def run_web(state: ModelRoutedState) -> dict[str, object]:
        question, _ = _validated_routed_state(state, expected_route="web")
        return {"result": web_handler(question)}

    graph = StateGraph(
        ModelRoutedState,
        input_schema=ModelRoutedInput,
        output_schema=ModelRoutedOutput,
    )
    graph.add_node("model_route", model_route)
    graph.add_node("policy", run_policy)
    graph.add_node("operations", run_operations)
    graph.add_node("web", run_web)

    graph.add_edge(START, "model_route")
    graph.add_conditional_edges(
        "model_route",
        selected_route,
        {
            "policy": "policy",
            "operations": "operations",
            "web": "web",
        },
    )
    graph.add_edge("policy", END)
    graph.add_edge("operations", END)
    graph.add_edge("web", END)

    return graph.compile(
        checkpointer=checkpointer,
        name="model_routed_capability_graph",
    )


def _validated_question(state: ModelRoutedState) -> str:
    question = state.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Graph state requires a non-empty question")
    return question


def _validated_model_decision(decision: object) -> RoutingDecision:
    expected_routes = (
        (PolicyRoutingDecision, "policy"),
        (OperationsRoutingDecision, "operations"),
        (WebRoutingDecision, "web"),
    )
    for decision_type, expected_route in expected_routes:
        if isinstance(decision, decision_type):
            if decision.route != expected_route:
                raise ValueError(
                    "Inconsistent routing decision: "
                    f"{decision_type.__name__} has route {decision.route!r}"
                )
            return cast(RoutingDecision, decision)

    raise TypeError("Model router must return a validated RoutingDecision")


def _validated_routed_state(
    state: ModelRoutedState,
    *,
    expected_route: Route | None = None,
) -> tuple[str, RoutingDecision]:
    question = _validated_question(state)
    decision = _validated_model_decision(state.get("routing_decision"))
    route = state.get("route")

    if route != decision.route:
        raise ValueError(
            "Inconsistent graph state: route does not match routing_decision"
        )
    if expected_route is not None and route != expected_route:
        raise ValueError(
            f"Inconsistent graph state: expected {expected_route!r} branch, "
            f"received {route!r}"
        )

    return question, decision


__all__ = [
    "ModelDecisionRouter",
    "ModelRoutedInput",
    "ModelRoutedOutput",
    "ModelRoutedState",
    "build_model_routed_capability_graph",
]
