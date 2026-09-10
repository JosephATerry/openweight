import json

import pytest
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool

from openweight_platform.orchestration.capabilities import (
    OperationsCapabilityAdapter,
    OperationsToolCall,
    PolicyCapabilityAdapter,
    WebCapabilityAdapter,
    build_capability_routing_graph,
)


def build_operations_tools(calls):
    def get_employee_record(identifier: str):
        calls["get_employee_record"].append({"identifier": identifier})
        return {
            "found": True,
            "record": {"employee_id": identifier},
        }

    def get_contractor_record(identifier: str):
        calls["get_contractor_record"].append({"identifier": identifier})
        return {
            "found": True,
            "record": {"contractor_id": identifier},
        }

    def get_access_request(request_id: str):
        calls["get_access_request"].append({"request_id": request_id})
        return {
            "found": True,
            "record": {"request_id": request_id},
        }

    return [
        StructuredTool.from_function(
            get_employee_record,
            name="get_employee_record",
            description="Fake employee lookup.",
        ),
        StructuredTool.from_function(
            get_contractor_record,
            name="get_contractor_record",
            description="Fake contractor lookup.",
        ),
        StructuredTool.from_function(
            get_access_request,
            name="get_access_request",
            description="Fake access-request lookup.",
        ),
    ]


def build_web_tool(calls, results):
    def search_web(query: str, max_results: int = 5):
        calls.append(
            {
                "query": query,
                "max_results": max_results,
            }
        )
        return results

    return StructuredTool.from_function(
        search_web,
        name="search_web",
        description="Fake public web search.",
    )


def empty_operations_calls():
    return {
        "get_employee_record": [],
        "get_contractor_record": [],
        "get_access_request": [],
    }


def test_policy_adapter_forwards_question_and_normalizes_documents():
    questions = []

    def retriever(question):
        questions.append(question)
        return [
            Document(
                page_content="Fictional policy evidence.",
                metadata={
                    "policy_id": "FIC-POLICY-001",
                    "chunk_index": 2,
                    "labels": ("access", "fictional"),
                },
            )
        ]

    adapter = PolicyCapabilityAdapter(retriever)
    result = adapter("What does the fictional policy require?")

    assert questions == ["What does the fictional policy require?"]
    assert result == {
        "evidence": [
            {
                "content": "Fictional policy evidence.",
                "metadata": {
                    "chunk_index": 2,
                    "labels": ["access", "fictional"],
                    "policy_id": "FIC-POLICY-001",
                },
            }
        ]
    }
    json.dumps(result)


def test_policy_adapter_represents_empty_retrieval_cleanly():
    adapter = PolicyCapabilityAdapter(lambda question: [])

    assert adapter("Question with no fictional evidence") == {"evidence": []}


def test_web_adapter_forwards_question_and_default_limit_and_preserves_results():
    calls = []
    web_results = [
        {
            "title": "Fictional Current Result",
            "url": "https://example.invalid/current",
            "content": "Fictional public information.",
            "score": 0.8,
        }
    ]
    adapter = WebCapabilityAdapter(build_web_tool(calls, web_results))

    result = adapter("What is the current fictional update?")

    assert calls == [
        {
            "query": "What is the current fictional update?",
            "max_results": 5,
        }
    ]
    assert result == {"results": web_results}


def test_web_adapter_represents_empty_results_cleanly():
    adapter = WebCapabilityAdapter(build_web_tool([], []))

    assert adapter("No fictional web matches") == {"results": []}


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_output"),
    [
        (
            "get_employee_record",
            {"identifier": "fic-emp-001"},
            {
                "found": True,
                "record": {"employee_id": "fic-emp-001"},
            },
        ),
        (
            "get_contractor_record",
            {"identifier": "fic-ctr-001"},
            {
                "found": True,
                "record": {"contractor_id": "fic-ctr-001"},
            },
        ),
        (
            "get_access_request",
            {"request_id": "fic-req-001"},
            {
                "found": True,
                "record": {"request_id": "fic-req-001"},
            },
        ),
    ],
)
def test_operations_adapter_selects_only_requested_tool(
    tool_name,
    arguments,
    expected_output,
):
    calls = empty_operations_calls()
    selector_questions = []

    def selector(question):
        selector_questions.append(question)
        return OperationsToolCall(
            tool_name=tool_name,
            arguments=arguments,
        )

    adapter = OperationsCapabilityAdapter(
        build_operations_tools(calls),
        selector,
    )
    question = "Find the selected fictional operations record"

    result = adapter(question)

    assert selector_questions == [question]
    assert calls[tool_name] == [arguments]
    assert all(
        calls[name] == []
        for name in calls
        if name != tool_name
    )
    assert result == {
        "tool_name": tool_name,
        "arguments": arguments,
        "output": expected_output,
    }


def test_operations_adapter_rejects_unsupported_tool_before_invocation():
    calls = empty_operations_calls()
    adapter = OperationsCapabilityAdapter(
        build_operations_tools(calls),
        lambda question: OperationsToolCall(
            tool_name="delete_employee_record",
            arguments={"identifier": "fic-emp-001"},
        ),
    )

    with pytest.raises(
        ValueError,
        match="Unsupported operations tool 'delete_employee_record'",
    ):
        adapter("Attempt an unsupported fictional operation")

    assert all(tool_calls == [] for tool_calls in calls.values())


@pytest.mark.parametrize("selected_route", ["policy", "operations", "web"])
def test_composed_graph_routes_only_to_selected_capability(selected_route):
    question = f"Route this fictional {selected_route} capability question"
    calls = {
        "router": [],
        "policy": [],
        "selector": [],
        "web": [],
    }
    operations_calls = empty_operations_calls()

    def router(received_question):
        calls["router"].append(received_question)
        return selected_route

    def policy_retriever(received_question):
        calls["policy"].append(received_question)
        return [
            Document(
                page_content="Composed fictional policy evidence.",
                metadata={"policy_id": "FIC-COMPOSED-001"},
            )
        ]

    def operations_selector(received_question):
        calls["selector"].append(received_question)
        return OperationsToolCall(
            tool_name="get_employee_record",
            arguments={"identifier": "fic-emp-001"},
        )

    web_results = [
        {
            "title": "Composed Fictional Web Result",
            "url": "https://example.invalid/composed",
            "content": "Composed fictional public information.",
            "score": None,
        }
    ]
    graph = build_capability_routing_graph(
        router=router,
        policy_retriever=policy_retriever,
        operations_tools=build_operations_tools(operations_calls),
        operations_selector=operations_selector,
        web_tool=build_web_tool(calls["web"], web_results),
    )

    result = graph.invoke({"question": question})

    expected_results = {
        "policy": {
            "evidence": [
                {
                    "content": "Composed fictional policy evidence.",
                    "metadata": {"policy_id": "FIC-COMPOSED-001"},
                }
            ]
        },
        "operations": {
            "tool_name": "get_employee_record",
            "arguments": {"identifier": "fic-emp-001"},
            "output": {
                "found": True,
                "record": {"employee_id": "fic-emp-001"},
            },
        },
        "web": {"results": web_results},
    }
    assert result == {
        "question": question,
        "route": selected_route,
        "result": expected_results[selected_route],
    }
    assert calls["router"] == [question]
    assert calls["policy"] == ([question] if selected_route == "policy" else [])
    assert calls["selector"] == (
        [question] if selected_route == "operations" else []
    )
    assert calls["web"] == (
        [
            {
                "query": question,
                "max_results": 5,
            }
        ]
        if selected_route == "web"
        else []
    )
    assert operations_calls["get_employee_record"] == (
        [{"identifier": "fic-emp-001"}]
        if selected_route == "operations"
        else []
    )
    assert operations_calls["get_contractor_record"] == []
    assert operations_calls["get_access_request"] == []
