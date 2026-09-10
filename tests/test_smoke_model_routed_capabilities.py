from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool

from openweight_platform.backends.base import GenerationResult, ModelBackend
from openweight_platform.orchestration.model_routing import (
    OperationsRoutingDecision,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "smoke_model_routed_capabilities.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "smoke_model_routed_capabilities",
    SCRIPT_PATH,
)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
smoke = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = smoke
SCRIPT_SPEC.loader.exec_module(smoke)


class FakeBackend(ModelBackend):
    def __init__(self, output: str) -> None:
        self.output = output
        self.events: list[str] = []
        self.prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    def load(self) -> None:
        self.events.append("load")

    def generate(self, prompt: str) -> GenerationResult:
        self.events.append("generate")
        self.prompts.append(prompt)
        return GenerationResult(
            text=self.output,
            input_tokens=10,
            output_tokens=5,
            generation_seconds=0.1,
            peak_vram_gib=0.0,
        )

    def unload(self) -> None:
        self.events.append("unload")


def fail_if_loaded(name, events):
    def fail():
        events.append(name)
        raise AssertionError(f"Unselected capability loaded: {name}")

    return fail


def test_cli_requires_question_and_has_no_manual_routing_arguments() -> None:
    with pytest.raises(SystemExit):
        smoke.parse_args([])

    destinations = {
        action.dest
        for action in smoke.build_parser()._actions
    }
    assert destinations == {
        "backend",
        "help",
        "question",
        "model_id",
        "max_new_tokens",
        "web_max_results",
        "muse_base_url",
        "muse_model_alias",
        "muse_timeout_seconds",
        "reasoning_strength",
    }
    with pytest.raises(SystemExit):
        smoke.parse_args(["--question", "test", "--route", "policy"])


def test_policy_route_initializes_only_policy_runtime(monkeypatch) -> None:
    events: list[object] = []

    class FakeConfig:
        @classmethod
        def from_env(cls):
            events.append("policy_config")
            return cls()

    class FakeEmbeddings:
        def __init__(self):
            events.append("embeddings")

    class FakeEngine:
        async def close(self):
            events.append("policy_engine_closed")

    vector_store = object()

    def create_store(config, embeddings, *, initialize):
        events.append(("vector_store", initialize))
        return FakeEngine(), vector_store

    def search_policy(store, question):
        events.append(("policy_search", store, question))
        return [
            Document(
                page_content="Fictional policy evidence.",
                metadata={"policy_id": "FIC-001"},
            )
        ]

    monkeypatch.setattr(
        smoke,
        "_load_policy_components",
        lambda: (FakeConfig, FakeEmbeddings, create_store, search_policy),
    )
    monkeypatch.setattr(
        smoke,
        "_load_operations_components",
        fail_if_loaded("operations", events),
    )
    monkeypatch.setattr(
        smoke,
        "_load_web_components",
        fail_if_loaded("web", events),
    )
    backend = FakeBackend('{"route":"policy"}')
    question = "What does the fictional policy require?"

    state = smoke.run_model_routed_smoke(question, backend)

    assert backend.events == ["load", "generate", "unload"]
    assert len(backend.prompts) == 1
    assert ("policy_search", vector_store, question) in events
    assert "policy_engine_closed" in events
    assert "operations" not in events
    assert "web" not in events
    assert state["route"] == "policy"
    assert state["result"] == {
        "evidence": [
            {
                "content": "Fictional policy evidence.",
                "metadata": {"policy_id": "FIC-001"},
            }
        ]
    }


def test_operations_route_builds_and_invokes_existing_tools_only(monkeypatch) -> None:
    events: list[object] = []

    class FakeConfig:
        @classmethod
        def from_env(cls):
            events.append("operations_config")
            return cls()

    class FakeService:
        def __init__(self, config):
            events.append("operations_service")

    def build_tools(service):
        events.append("build_operational_tools")

        def get_employee_record(identifier: str):
            events.append(("employee_lookup", identifier))
            return {
                "found": True,
                "record": {"employee_id": identifier},
            }

        def get_contractor_record(identifier: str):
            raise AssertionError("Contractor tool must not execute")

        def get_access_request(request_id: str):
            raise AssertionError("Request tool must not execute")

        return [
            StructuredTool.from_function(
                func=function,
                name=function.__name__,
                description=f"Fake {function.__name__}.",
            )
            for function in (
                get_employee_record,
                get_contractor_record,
                get_access_request,
            )
        ]

    monkeypatch.setattr(
        smoke,
        "_load_operations_components",
        lambda: (FakeConfig, FakeService, build_tools),
    )
    monkeypatch.setattr(
        smoke,
        "_load_policy_components",
        fail_if_loaded("policy", events),
    )
    monkeypatch.setattr(
        smoke,
        "_load_web_components",
        fail_if_loaded("web", events),
    )
    backend = FakeBackend(
        '{"route":"operations","tool_name":"get_employee_record",'
        '"arguments":{"identifier":"fic-emp-001"}}'
    )

    state = smoke.run_model_routed_smoke(
        "Look up employee fic-emp-001.",
        backend,
    )

    assert backend.events == ["load", "generate", "unload"]
    assert len(backend.prompts) == 1
    assert events == [
        "operations_config",
        "operations_service",
        "build_operational_tools",
        ("employee_lookup", "fic-emp-001"),
    ]
    assert state["result"] == {
        "tool_name": "get_employee_record",
        "arguments": {"identifier": "fic-emp-001"},
        "output": {
            "found": True,
            "record": {"employee_id": "fic-emp-001"},
        },
    }


def test_web_route_initializes_only_existing_web_service_and_tool(
    monkeypatch,
) -> None:
    events: list[object] = []

    class FakeWebService:
        def __init__(self):
            events.append("web_service")

    def build_web_tool(service):
        events.append("build_web_search_tool")

        def search_web(query: str, max_results: int = 5):
            events.append(("web_search", query, max_results))
            return [{"title": "Fictional current result", "score": None}]

        return StructuredTool.from_function(
            func=search_web,
            name="search_web",
            description="Fake external public search.",
        )

    monkeypatch.setattr(
        smoke,
        "_load_web_components",
        lambda: (FakeWebService, build_web_tool),
    )
    monkeypatch.setattr(
        smoke,
        "_load_policy_components",
        fail_if_loaded("policy", events),
    )
    monkeypatch.setattr(
        smoke,
        "_load_operations_components",
        fail_if_loaded("operations", events),
    )
    backend = FakeBackend('{"route":"web"}')
    question = "What is the latest fictional public guidance?"

    state = smoke.run_model_routed_smoke(
        question,
        backend,
        web_max_results=3,
    )

    assert backend.events == ["load", "generate", "unload"]
    assert len(backend.prompts) == 1
    assert events == [
        "web_service",
        "build_web_search_tool",
        ("web_search", question, 3),
    ]
    assert state["result"] == {
        "results": [{"title": "Fictional current result", "score": None}]
    }


def test_graph_receives_only_question_and_routes_model_once(monkeypatch) -> None:
    invocations = []
    routers = []

    class FakeGraph:
        def __init__(self, model_router):
            self.model_router = model_router

        def invoke(self, graph_input):
            invocations.append(graph_input)
            decision = self.model_router.decide(graph_input["question"])
            return {
                "question": graph_input["question"],
                "routing_decision": decision,
                "route": decision.route,
                "result": {},
            }

    def build_graph(**kwargs):
        routers.append(kwargs["model_router"])
        return FakeGraph(kwargs["model_router"])

    monkeypatch.setattr(
        smoke,
        "build_model_routed_capability_graph",
        build_graph,
    )
    backend = FakeBackend('{"route":"policy"}')
    question = "Preserve this original question."

    smoke.run_model_routed_smoke(question, backend)

    assert invocations == [{"question": question}]
    assert len(routers) == 1
    assert len(backend.prompts) == 1
    assert backend.events == ["load", "generate", "unload"]


def test_unload_occurs_when_graph_invocation_raises(monkeypatch) -> None:
    class FailingGraph:
        def invoke(self, graph_input):
            raise RuntimeError("fictional graph failure")

    monkeypatch.setattr(
        smoke,
        "build_model_routed_capability_graph",
        lambda **kwargs: FailingGraph(),
    )
    backend = FakeBackend('{"route":"policy"}')

    with pytest.raises(RuntimeError, match="fictional graph failure"):
        smoke.run_model_routed_smoke("Trigger failure.", backend)

    assert backend.events == ["load", "unload"]


def test_final_state_serializes_routing_decision_and_dates_cleanly() -> None:
    decision = OperationsRoutingDecision(
        route="operations",
        tool_name="get_employee_record",
        arguments={"identifier": "fic-emp-001"},
    )
    state = {
        "question": "Look up employee fic-emp-001.",
        "routing_decision": decision,
        "route": "operations",
        "result": {
            "record": {
                "employee_id": "fic-emp-001",
                "effective_date": date(2026, 8, 19),
            }
        },
    }

    document = smoke.serialize_final_state(state)

    assert document == {
        "question": "Look up employee fic-emp-001.",
        "routing_decision": {
            "route": "operations",
            "tool_name": "get_employee_record",
            "arguments": {"identifier": "fic-emp-001"},
        },
        "route": "operations",
        "result": {
            "record": {
                "employee_id": "fic-emp-001",
                "effective_date": "2026-08-19",
            }
        },
    }
    assert json.loads(json.dumps(document)) == document


def test_main_prints_json_final_state(monkeypatch, capsys) -> None:
    decision = OperationsRoutingDecision(
        route="operations",
        tool_name="get_employee_record",
        arguments={"identifier": "fic-emp-001"},
    )
    backend = FakeBackend("unused")
    monkeypatch.setattr(smoke, "_create_backend", lambda *args: backend)
    monkeypatch.setattr(
        smoke,
        "run_model_routed_smoke",
        lambda question, received_backend, **kwargs: {
            "question": question,
            "routing_decision": decision,
            "route": "operations",
            "result": {"found": True},
        },
    )

    assert smoke.main(["--question", "Look up employee fic-emp-001."]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output == {
        "question": "Look up employee fic-emp-001.",
        "routing_decision": {
            "route": "operations",
            "tool_name": "get_employee_record",
            "arguments": {"identifier": "fic-emp-001"},
        },
        "route": "operations",
        "result": {"found": True},
    }
