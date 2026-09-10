"""Minimal conditional routing with LangGraph."""

from collections.abc import Callable
from typing import Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


Route = Literal["policy", "operations", "web"]
Router = Callable[[str], Route]
Handler = Callable[[str], object]

SUPPORTED_ROUTES: tuple[Route, ...] = (
    "policy",
    "operations",
    "web",
)


class RoutingInput(TypedDict):
    question: str


class RoutingState(RoutingInput, total=False):
    route: Route
    result: object


class RoutingOutput(TypedDict):
    question: str
    route: Route
    result: object


def build_routing_graph(
    router: Router,
    policy_handler: Handler,
    operations_handler: Handler,
    web_handler: Handler,
) -> CompiledStateGraph:
    """Build a local graph that routes one question to one injected handler."""

    def route_input(state: RoutingState) -> dict[str, Route]:
        route = router(state["question"])

        if route not in SUPPORTED_ROUTES:
            expected = ", ".join(SUPPORTED_ROUTES)
            raise ValueError(
                f"Unsupported route {route!r}; expected one of: {expected}"
            )

        return {"route": cast(Route, route)}

    def selected_route(state: RoutingState) -> Route:
        return state["route"]

    def run_policy(state: RoutingState) -> dict[str, object]:
        return {"result": policy_handler(state["question"])}

    def run_operations(state: RoutingState) -> dict[str, object]:
        return {"result": operations_handler(state["question"])}

    def run_web(state: RoutingState) -> dict[str, object]:
        return {"result": web_handler(state["question"])}

    graph = StateGraph(
        RoutingState,
        input_schema=RoutingInput,
        output_schema=RoutingOutput,
    )
    graph.add_node("route_input", route_input)
    graph.add_node("policy", run_policy)
    graph.add_node("operations", run_operations)
    graph.add_node("web", run_web)

    graph.add_edge(START, "route_input")
    graph.add_conditional_edges(
        "route_input",
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

    return graph.compile(name="routing_graph")


__all__ = [
    "Handler",
    "Route",
    "Router",
    "RoutingInput",
    "RoutingOutput",
    "RoutingState",
    "SUPPORTED_ROUTES",
    "build_routing_graph",
]
