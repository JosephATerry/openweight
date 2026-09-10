#!/usr/bin/env python3
"""Run one model-selected real capability through the existing LangGraph."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from openweight_platform.backends.base import ModelBackend
from openweight_platform.backends.factory import (
    DEFAULT_BACKEND_NAME,
    DEFAULT_GPT_OSS_MODEL_ID,
    ModelBackendConfig,
    SUPPORTED_BACKENDS,
    build_model_backend,
)
from openweight_platform.backends.muse_glimmer import (
    DEFAULT_MUSE_BASE_URL,
    DEFAULT_MUSE_MODEL_ALIAS,
    DEFAULT_MUSE_TIMEOUT_SECONDS,
    SUPPORTED_REASONING_STRENGTHS,
)
from openweight_platform.orchestration.model_graph import (
    build_model_routed_capability_graph,
)
from openweight_platform.orchestration.model_routing import ModelRouter


def _load_policy_components() -> tuple[Any, Any, Any, Any]:
    from openweight_platform.rag.database import PostgresConfig
    from openweight_platform.rag.embeddings import QwenEmbeddings
    from openweight_platform.rag.retrieval import search_policy_corpus
    from openweight_platform.rag.vectorstore import create_policy_vector_store

    return (
        PostgresConfig,
        QwenEmbeddings,
        create_policy_vector_store,
        search_policy_corpus,
    )


def _load_operations_components() -> tuple[Any, Any, Any]:
    from openweight_platform.operations.service import OperationsLookupService
    from openweight_platform.operations.tools import build_operational_tools
    from openweight_platform.rag.database import PostgresConfig

    return PostgresConfig, OperationsLookupService, build_operational_tools


def _load_web_components() -> tuple[Any, Any]:
    from openweight_platform.web.search import TavilySearchService
    from openweight_platform.web.tools import build_web_search_tool

    return TavilySearchService, build_web_search_tool


class _LazyPolicyRetriever:
    def __init__(self) -> None:
        self._engine: Any | None = None
        self._vector_store: Any | None = None
        self._search: Any | None = None

    def __call__(self, question: str) -> list[object]:
        if self._vector_store is None:
            (
                config_type,
                embeddings_type,
                vector_store_factory,
                self._search,
            ) = _load_policy_components()
            config = config_type.from_env()
            embeddings = embeddings_type()
            self._engine, self._vector_store = vector_store_factory(
                config,
                embeddings,
                initialize=False,
            )

        return self._search(self._vector_store, question)

    def close(self) -> None:
        if self._engine is not None:
            engine = self._engine
            self._engine = None
            asyncio.run(engine.close())


class _LazyOperationsTools:
    def __init__(self) -> None:
        self._real_tools: dict[str, BaseTool] | None = None

    def build_tools(self) -> list[BaseTool]:
        def get_employee_record(identifier: str) -> object:
            return self._invoke(
                "get_employee_record",
                {"identifier": identifier},
            )

        def get_contractor_record(identifier: str) -> object:
            return self._invoke(
                "get_contractor_record",
                {"identifier": identifier},
            )

        def get_access_request(request_id: str) -> object:
            return self._invoke(
                "get_access_request",
                {"request_id": request_id},
            )

        functions = (
            get_employee_record,
            get_contractor_record,
            get_access_request,
        )
        return [
            StructuredTool.from_function(
                func=function,
                name=function.__name__,
                description=(
                    f"Lazily delegate {function.__name__} to the existing "
                    "operations tool."
                ),
            )
            for function in functions
        ]

    def _invoke(self, tool_name: str, arguments: dict[str, object]) -> object:
        if self._real_tools is None:
            config_type, service_type, tools_factory = (
                _load_operations_components()
            )
            config = config_type.from_env()
            service = service_type(config)
            self._real_tools = {
                tool.name: tool
                for tool in tools_factory(service)
            }

        try:
            tool = self._real_tools[tool_name]
        except KeyError as error:
            raise RuntimeError(
                f"Existing operations tool is unavailable: {tool_name}"
            ) from error
        return tool.invoke(arguments)


class _LazyWebTool:
    def __init__(self) -> None:
        self._real_tool: BaseTool | None = None

    def build_tool(self) -> BaseTool:
        def search_web(query: str, max_results: int = 5) -> object:
            return self._get_real_tool().invoke(
                {
                    "query": query,
                    "max_results": max_results,
                }
            )

        return StructuredTool.from_function(
            func=search_web,
            name="search_web",
            description=(
                "Lazily delegate current external/public web search to the "
                "existing search_web tool."
            ),
        )

    def _get_real_tool(self) -> BaseTool:
        if self._real_tool is None:
            service_type, tool_factory = _load_web_components()
            service = service_type()
            self._real_tool = tool_factory(service)
        return self._real_tool


def run_model_routed_smoke(
    question: str,
    backend: ModelBackend,
    *,
    web_max_results: int = 5,
) -> dict[str, object]:
    """Load one backend, route once, and execute only the selected branch."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    policy_retriever = _LazyPolicyRetriever()
    operations_tools = _LazyOperationsTools()
    web_tool = _LazyWebTool()
    graph = build_model_routed_capability_graph(
        model_router=ModelRouter(backend),
        policy_retriever=policy_retriever,
        operations_tools=operations_tools.build_tools(),
        web_tool=web_tool.build_tool(),
        web_max_results=web_max_results,
    )

    backend.load()
    try:
        return graph.invoke({"question": question})
    finally:
        try:
            backend.unload()
        finally:
            policy_retriever.close()


def serialize_final_state(state: Mapping[str, object]) -> dict[str, object]:
    """Convert the graph's typed final state to JSON-compatible values."""
    return {
        "question": _json_value(state["question"]),
        "routing_decision": _json_value(state["routing_decision"]),
        "route": _json_value(state["route"]),
        "result": _json_value(state["result"]),
    }


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_value(item) for item in value]
    return str(value)


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route one question with a local model and run one capability.",
    )
    parser.add_argument(
        "--backend",
        choices=SUPPORTED_BACKENDS,
        default=DEFAULT_BACKEND_NAME,
        help="Local model backend (default: gpt-oss).",
    )
    parser.add_argument(
        "--question",
        required=True,
        help="User question supplied unchanged to the model-routed graph.",
    )
    parser.add_argument(
        "--model-id",
        default=DEFAULT_GPT_OSS_MODEL_ID,
        help="GPT-OSS Hugging Face model ID or local model path.",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=_positive_integer,
        default=256,
        help="Maximum tokens for the routing decision (default: 256).",
    )
    parser.add_argument(
        "--web-max-results",
        type=_positive_integer,
        default=5,
        help="Maximum Tavily results if the web route is selected (default: 5).",
    )
    parser.add_argument(
        "--muse-base-url",
        default=DEFAULT_MUSE_BASE_URL,
        help="Local llama.cpp OpenAI-compatible /v1 base URL.",
    )
    parser.add_argument(
        "--muse-model-alias",
        default=DEFAULT_MUSE_MODEL_ALIAS,
        help="Model alias sent to the local llama.cpp server.",
    )
    parser.add_argument(
        "--muse-timeout-seconds",
        type=float,
        default=DEFAULT_MUSE_TIMEOUT_SECONDS,
        help="Timeout for one local Muse generation request.",
    )
    parser.add_argument(
        "--reasoning-strength",
        choices=SUPPORTED_REASONING_STRENGTHS,
        default="low",
        help="Muse reasoning effort (default: low).",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _create_backend(args: argparse.Namespace) -> ModelBackend:
    return build_model_backend(
        ModelBackendConfig(
            backend_name=args.backend,
            max_new_tokens=args.max_new_tokens,
            gpt_oss_model_id=args.model_id,
            muse_base_url=args.muse_base_url,
            muse_model_alias=args.muse_model_alias,
            muse_timeout_seconds=args.muse_timeout_seconds,
            reasoning_strength=args.reasoning_strength,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    backend = _create_backend(args)
    state = run_model_routed_smoke(
        args.question,
        backend,
        web_max_results=args.web_max_results,
    )
    print(
        json.dumps(
            serialize_final_state(state),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
