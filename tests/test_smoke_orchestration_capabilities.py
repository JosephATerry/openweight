from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool

from openweight_platform.orchestration.capabilities import OperationsToolCall


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "smoke_orchestration_capabilities.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "smoke_orchestration_capabilities",
    SCRIPT_PATH,
)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
smoke = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(smoke)


class FakeConfig:
    calls = 0

    @classmethod
    def from_env(cls) -> "FakeConfig":
        cls.calls += 1
        return cls()


@pytest.fixture(autouse=True)
def reset_fake_config() -> None:
    FakeConfig.calls = 0


def test_policy_smoke_wires_real_composition_with_fake_runtime(monkeypatch) -> None:
    events: list[object] = []

    class FakeEmbeddings:
        def __init__(self) -> None:
            events.append("embeddings")

    class FakeEngine:
        async def close(self) -> None:
            events.append("engine_closed")

    vector_store = object()

    def create_vector_store(config, embeddings, *, initialize):
        events.append(("vector_store", config, embeddings, initialize))
        return FakeEngine(), vector_store

    def search_policy(store, question):
        events.append(("search", store, question))
        return [
            Document(
                page_content="Privileged access requires MFA.",
                metadata={"section": "access-control"},
            )
        ]

    monkeypatch.setattr(
        smoke,
        "_load_policy_components",
        lambda: (FakeConfig, FakeEmbeddings, create_vector_store, search_policy),
    )

    state = smoke.run_policy_smoke("What is required?")

    assert FakeConfig.calls == 1
    assert ("search", vector_store, "What is required?") in events
    assert events[-1] == "engine_closed"
    assert state == {
        "question": "What is required?",
        "route": "policy",
        "result": {
            "evidence": [
                {
                    "content": "Privileged access requires MFA.",
                    "metadata": {"section": "access-control"},
                }
            ]
        },
    }


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_employee_record", {"identifier": "fic-emp-001"}),
        ("get_contractor_record", {"identifier": "fic-con-001"}),
        ("get_access_request", {"request_id": "fic-req-001"}),
    ],
)
def test_operations_smoke_wires_selected_real_tool_with_fake_runtime(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeService:
        def __init__(self, config) -> None:
            self.config = config

    def build_tools(service):
        def make_tool(name):
            if name == "get_access_request":
                def invoke(request_id: str):
                    calls.append((name, {"request_id": request_id}))
                    return {"record_id": request_id}
            else:
                def invoke(identifier: str):
                    calls.append((name, {"identifier": identifier}))
                    return {"record_id": identifier}

            return StructuredTool.from_function(
                func=invoke,
                name=name,
                description=f"Fake {name} lookup.",
            )

        return [make_tool(name) for name in smoke.OPERATIONS_TOOL_NAMES]

    monkeypatch.setattr(
        smoke,
        "_load_operations_components",
        lambda: (FakeConfig, FakeService, build_tools),
    )

    tool_call = OperationsToolCall(tool_name=tool_name, arguments=arguments)
    state = smoke.run_operations_smoke("Find the record", tool_call)

    assert FakeConfig.calls == 1
    assert calls == [(tool_name, arguments)]
    assert state == {
        "question": "Find the record",
        "route": "operations",
        "result": {
            "tool_name": tool_name,
            "arguments": arguments,
            "output": {"record_id": next(iter(arguments.values()))},
        },
    }


def test_web_smoke_wires_real_tool_factory_with_fake_runtime(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeSearchService:
        pass

    def build_search_tool(service):
        assert isinstance(service, FakeSearchService)

        def search_web(query: str, max_results: int = 5):
            calls.append((query, max_results))
            return [
                {
                    "title": "NIST guidance",
                    "url": "https://example.invalid/nist",
                    "content": "Fictional test result",
                    "score": 0.9,
                }
            ]

        return StructuredTool.from_function(
            func=search_web,
            name="search_web",
            description="Search current external public web information.",
        )

    monkeypatch.setattr(
        smoke,
        "_load_web_components",
        lambda: (FakeSearchService, build_search_tool),
    )

    state = smoke.run_web_smoke("Latest guidance?", max_results=3)

    assert calls == [("Latest guidance?", 3)]
    assert state == {
        "question": "Latest guidance?",
        "route": "web",
        "result": {
            "results": [
                {
                    "title": "NIST guidance",
                    "url": "https://example.invalid/nist",
                    "content": "Fictional test result",
                    "score": 0.9,
                }
            ]
        },
    }


def test_route_is_required() -> None:
    with pytest.raises(SystemExit):
        smoke.parse_args([])


@pytest.mark.parametrize(
    "argv",
    [
        ["--route", "operations"],
        ["--route", "operations", "--operations-tool", "get_employee_record"],
        ["--route", "operations", "--operations-tool", "get_access_request"],
        ["--route", "web", "--max-results", "0"],
    ],
)
def test_invalid_route_specific_arguments_fail_clearly(argv) -> None:
    with pytest.raises(SystemExit):
        smoke.parse_args(argv)


@pytest.mark.parametrize(
    ("tool_name", "flag", "value", "expected_arguments"),
    [
        (
            "get_employee_record",
            "--identifier",
            "fic-emp-001",
            {"identifier": "fic-emp-001"},
        ),
        (
            "get_contractor_record",
            "--identifier",
            "fic-con-001",
            {"identifier": "fic-con-001"},
        ),
        (
            "get_access_request",
            "--request-id",
            "fic-req-001",
            {"request_id": "fic-req-001"},
        ),
    ],
)
def test_explicit_cli_selection_becomes_operations_tool_call(
    tool_name,
    flag,
    value,
    expected_arguments,
) -> None:
    args = smoke.parse_args(
        ["--route", "operations", "--operations-tool", tool_name, flag, value]
    )

    assert smoke.build_operations_tool_call(args) == OperationsToolCall(
        tool_name=tool_name,
        arguments=expected_arguments,
    )


@pytest.mark.parametrize("route", ["policy", "operations", "web"])
def test_main_dispatches_only_the_explicit_route(monkeypatch, capsys, route) -> None:
    calls: list[tuple[str, object]] = []

    def policy(question):
        calls.append(("policy", question))
        return {"question": question, "route": "policy", "result": {}}

    def operations(question, tool_call):
        calls.append(("operations", tool_call))
        return {"question": question, "route": "operations", "result": {}}

    def web(question, *, max_results):
        calls.append(("web", max_results))
        return {"question": question, "route": "web", "result": {}}

    monkeypatch.setattr(smoke, "run_policy_smoke", policy)
    monkeypatch.setattr(smoke, "run_operations_smoke", operations)
    monkeypatch.setattr(smoke, "run_web_smoke", web)

    argv = ["--route", route]
    if route == "operations":
        argv.extend(
            [
                "--operations-tool",
                "get_employee_record",
                "--identifier",
                "fic-emp-001",
            ]
        )

    assert smoke.main(argv) == 0
    output = json.loads(capsys.readouterr().out)

    assert len(calls) == 1
    assert calls[0][0] == route
    assert output["route"] == route
