"""Run one real platform capability through the composed routing graph.

This is an explicit smoke runner, not an autonomous router. Each invocation
initializes only the infrastructure needed by the selected route.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from openweight_platform.orchestration.capabilities import (
    OperationsToolCall,
    build_capability_routing_graph,
)


POLICY_QUESTION = "What does the privileged access policy require?"
OPERATIONS_QUESTION = "Look up the explicitly selected fictional operations record."
WEB_QUESTION = "What is the latest NIST guidance for generative AI security?"

OPERATIONS_TOOL_NAMES = (
    "get_employee_record",
    "get_contractor_record",
    "get_access_request",
)


def _load_policy_components() -> tuple[Any, Any, Any, Any]:
    """Load policy dependencies only when the policy route is selected."""
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
    """Load operations dependencies only when that route is selected."""
    from openweight_platform.operations.service import OperationsLookupService
    from openweight_platform.operations.tools import build_operational_tools
    from openweight_platform.rag.database import PostgresConfig

    return PostgresConfig, OperationsLookupService, build_operational_tools


def _load_web_components() -> tuple[Any, Any]:
    """Load Tavily dependencies only when the web route is selected."""
    from openweight_platform.web.search import TavilySearchService
    from openweight_platform.web.tools import build_web_search_tool

    return TavilySearchService, build_web_search_tool


def _unavailable_policy_retriever(question: str) -> list[object]:
    raise RuntimeError("Policy capability was not initialized for this route.")


def _unavailable_operations_selector(question: str) -> OperationsToolCall:
    raise RuntimeError("Operations capability was not initialized for this route.")


def _build_unavailable_web_tool() -> BaseTool:
    def unavailable_web(query: str, max_results: int = 5) -> list[object]:
        raise RuntimeError("Web capability was not initialized for this route.")

    return StructuredTool.from_function(
        func=unavailable_web,
        name="search_web",
        description="Unavailable web search placeholder for an unselected route.",
    )


def run_policy_smoke(question: str) -> dict[str, object]:
    """Run real policy retrieval through the existing composed graph."""
    (
        config_type,
        embeddings_type,
        vector_store_factory,
        policy_search,
    ) = _load_policy_components()

    engine = None
    try:
        config = config_type.from_env()
        embeddings = embeddings_type()
        engine, vector_store = vector_store_factory(
            config,
            embeddings,
            initialize=False,
        )

        def retrieve_policy(original_question: str) -> list[object]:
            return policy_search(vector_store, original_question)

        graph = build_capability_routing_graph(
            router=lambda original_question: "policy",
            policy_retriever=retrieve_policy,
            operations_tools=(),
            operations_selector=_unavailable_operations_selector,
            web_tool=_build_unavailable_web_tool(),
        )
        return graph.invoke({"question": question})
    finally:
        if engine is not None:
            asyncio.run(engine.close())


def run_operations_smoke(
    question: str,
    tool_call: OperationsToolCall,
) -> dict[str, object]:
    """Run one explicitly selected real operations tool through the graph."""
    config_type, service_type, tools_factory = _load_operations_components()
    config = config_type.from_env()
    service = service_type(config)
    operations_tools = tools_factory(service)

    graph = build_capability_routing_graph(
        router=lambda original_question: "operations",
        policy_retriever=_unavailable_policy_retriever,
        operations_tools=operations_tools,
        operations_selector=lambda original_question: tool_call,
        web_tool=_build_unavailable_web_tool(),
    )
    return graph.invoke({"question": question})


def run_web_smoke(question: str, *, max_results: int = 5) -> dict[str, object]:
    """Run real Tavily-backed search through the existing composed graph."""
    service_type, tool_factory = _load_web_components()
    service = service_type()
    web_tool = tool_factory(service)

    graph = build_capability_routing_graph(
        router=lambda original_question: "web",
        policy_retriever=_unavailable_policy_retriever,
        operations_tools=(),
        operations_selector=_unavailable_operations_selector,
        web_tool=web_tool,
        web_max_results=max_results,
    )
    return graph.invoke({"question": question})


def build_operations_tool_call(args: argparse.Namespace) -> OperationsToolCall:
    """Convert an explicit operations CLI choice to the adapter contract."""
    if args.operations_tool == "get_access_request":
        arguments: dict[str, object] = {"request_id": args.request_id}
    else:
        arguments = {"identifier": args.identifier}

    return OperationsToolCall(
        tool_name=args.operations_tool,
        arguments=arguments,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one explicitly selected real capability through LangGraph."
    )
    parser.add_argument(
        "--route",
        required=True,
        choices=("policy", "operations", "web"),
        help="Capability route to run; no automatic routing is performed.",
    )
    parser.add_argument(
        "--question",
        help="Question to pass through the graph (route-specific default if omitted).",
    )
    parser.add_argument(
        "--operations-tool",
        choices=OPERATIONS_TOOL_NAMES,
        help="Required for the operations route.",
    )
    parser.add_argument(
        "--identifier",
        help="Employee or contractor ID/name for the selected operations tool.",
    )
    parser.add_argument(
        "--request-id",
        help="Access request ID for get_access_request.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Maximum web results (default: 5).",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.route == "operations":
        if args.operations_tool is None:
            parser.error("--operations-tool is required when --route operations is used")
        if args.operations_tool == "get_access_request":
            if not args.request_id:
                parser.error("--request-id is required for get_access_request")
        elif not args.identifier:
            parser.error(
                "--identifier is required for employee and contractor lookups"
            )

    if args.route == "web" and args.max_results <= 0:
        parser.error("--max-results must be greater than zero")

    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.route == "policy":
        state = run_policy_smoke(args.question or POLICY_QUESTION)
    elif args.route == "operations":
        state = run_operations_smoke(
            args.question or OPERATIONS_QUESTION,
            build_operations_tool_call(args),
        )
    else:
        state = run_web_smoke(
            args.question or WEB_QUESTION,
            max_results=args.max_results,
        )

    print(json.dumps(state, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
