"""Capability adapters for the generic routing graph."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias, TypedDict

from langchain_core.documents import Document
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from openweight_platform.orchestration.routing import (
    Router,
    build_routing_graph,
)


JsonValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)
PolicyRetriever = Callable[[str], Sequence[Document]]

SUPPORTED_OPERATIONS_TOOLS = (
    "get_employee_record",
    "get_contractor_record",
    "get_access_request",
)


class PolicyEvidence(TypedDict):
    content: str
    metadata: dict[str, JsonValue]


class PolicyCapabilityResult(TypedDict):
    evidence: list[PolicyEvidence]


class WebCapabilityResult(TypedDict):
    results: object


@dataclass(frozen=True)
class OperationsToolCall:
    tool_name: str
    arguments: dict[str, object]


OperationsSelector = Callable[[str], OperationsToolCall]


class OperationsCapabilityResult(TypedDict):
    tool_name: str
    arguments: dict[str, object]
    output: object


@dataclass(frozen=True)
class PolicyCapabilityAdapter:
    retriever: PolicyRetriever

    def __call__(self, question: str) -> PolicyCapabilityResult:
        documents = self.retriever(question)
        return {
            "evidence": [
                {
                    "content": document.page_content,
                    "metadata": _json_mapping(document.metadata),
                }
                for document in documents
            ]
        }


@dataclass(frozen=True)
class WebCapabilityAdapter:
    tool: BaseTool
    max_results: int = 5

    def __post_init__(self) -> None:
        if self.max_results <= 0:
            raise ValueError("max_results must be greater than zero")

    def __call__(self, question: str) -> WebCapabilityResult:
        results = self.tool.invoke(
            {
                "query": question,
                "max_results": self.max_results,
            }
        )
        return {"results": results}


class OperationsCapabilityAdapter:
    def __init__(
        self,
        tools: Sequence[BaseTool],
        selector: OperationsSelector,
    ) -> None:
        self._tools_by_name = {tool.name: tool for tool in tools}
        self._selector = selector

    def __call__(self, question: str) -> OperationsCapabilityResult:
        tool_call = self._selector(question)

        if tool_call.tool_name not in SUPPORTED_OPERATIONS_TOOLS:
            expected = ", ".join(SUPPORTED_OPERATIONS_TOOLS)
            raise ValueError(
                f"Unsupported operations tool {tool_call.tool_name!r}; "
                f"expected one of: {expected}"
            )

        tool = self._tools_by_name.get(tool_call.tool_name)
        if tool is None:
            raise ValueError(
                f"Operations tool {tool_call.tool_name!r} is not available"
            )

        arguments = dict(tool_call.arguments)
        output = tool.invoke(arguments)
        return {
            "tool_name": tool_call.tool_name,
            "arguments": dict(tool_call.arguments),
            "output": output,
        }


def build_capability_routing_graph(
    router: Router,
    policy_retriever: PolicyRetriever,
    operations_tools: Sequence[BaseTool],
    operations_selector: OperationsSelector,
    web_tool: BaseTool,
    *,
    web_max_results: int = 5,
) -> CompiledStateGraph:
    """Compose platform capability adapters with the generic routing graph."""

    return build_routing_graph(
        router=router,
        policy_handler=PolicyCapabilityAdapter(policy_retriever),
        operations_handler=OperationsCapabilityAdapter(
            operations_tools,
            operations_selector,
        ),
        web_handler=WebCapabilityAdapter(
            web_tool,
            max_results=web_max_results,
        ),
    )


def _json_mapping(values: Mapping[object, object]) -> dict[str, JsonValue]:
    return {
        str(key): _json_value(value)
        for key, value in sorted(
            values.items(),
            key=lambda item: str(item[0]),
        )
    }


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, Mapping):
        return _json_mapping(value)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_value(item) for item in value]

    return str(value)


__all__ = [
    "OperationsCapabilityAdapter",
    "OperationsCapabilityResult",
    "OperationsSelector",
    "OperationsToolCall",
    "PolicyCapabilityAdapter",
    "PolicyCapabilityResult",
    "PolicyEvidence",
    "PolicyRetriever",
    "SUPPORTED_OPERATIONS_TOOLS",
    "WebCapabilityAdapter",
    "WebCapabilityResult",
    "build_capability_routing_graph",
]
