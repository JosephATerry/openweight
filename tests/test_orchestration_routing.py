from typing import get_args

import pytest
from langgraph.graph.state import CompiledStateGraph

from openweight_platform.orchestration.routing import (
    Route,
    RoutingInput,
    RoutingOutput,
    RoutingState,
    build_routing_graph,
)


def build_recording_graph(selected_route, handler_results):
    calls = {
        "router": [],
        "policy": [],
        "operations": [],
        "web": [],
    }

    def router(question):
        calls["router"].append(question)
        return selected_route

    def policy_handler(question):
        calls["policy"].append(question)
        return handler_results["policy"]

    def operations_handler(question):
        calls["operations"].append(question)
        return handler_results["operations"]

    def web_handler(question):
        calls["web"].append(question)
        return handler_results["web"]

    graph = build_routing_graph(
        router=router,
        policy_handler=policy_handler,
        operations_handler=operations_handler,
        web_handler=web_handler,
    )
    return graph, calls


def test_route_and_state_schemas_are_typed_for_minimal_input_and_full_output():
    assert get_args(Route) == ("policy", "operations", "web")
    assert RoutingInput.__required_keys__ == frozenset({"question"})
    assert RoutingState.__required_keys__ == frozenset({"question"})
    assert RoutingState.__optional_keys__ == frozenset({"route", "result"})
    assert RoutingOutput.__required_keys__ == frozenset(
        {"question", "route", "result"}
    )


def test_compiled_graph_can_be_invoked_with_only_a_question():
    graph, _ = build_recording_graph(
        "policy",
        {
            "policy": {"answer": "fictional policy result"},
            "operations": {"record": "fictional operations result"},
            "web": {"results": []},
        },
    )

    result = graph.invoke({"question": "What is the fictional policy?"})

    assert isinstance(graph, CompiledStateGraph)
    assert result["route"] == "policy"


@pytest.mark.parametrize("selected_route", ["policy", "operations", "web"])
def test_selected_route_executes_only_its_handler(selected_route):
    question = f"Route this fictional {selected_route} question"
    handler_results = {
        "policy": {"answer": "fictional policy result"},
        "operations": {"record": "fictional operations result"},
        "web": {"results": [{"title": "fictional web result"}]},
    }
    graph, calls = build_recording_graph(selected_route, handler_results)

    result = graph.invoke({"question": question})

    assert calls["router"] == [question]
    assert calls[selected_route] == [question]
    assert all(
        calls[route] == []
        for route in ("policy", "operations", "web")
        if route != selected_route
    )
    assert result == {
        "question": question,
        "route": selected_route,
        "result": handler_results[selected_route],
    }


def test_invalid_router_output_fails_before_any_handler_runs():
    graph, calls = build_recording_graph(
        "unsupported",
        {
            "policy": {"answer": "unused"},
            "operations": {"record": "unused"},
            "web": {"results": []},
        },
    )

    with pytest.raises(ValueError, match="Unsupported route 'unsupported'"):
        graph.invoke({"question": "Route this invalid fictional question"})

    assert calls["router"] == ["Route this invalid fictional question"]
    assert calls["policy"] == []
    assert calls["operations"] == []
    assert calls["web"] == []
