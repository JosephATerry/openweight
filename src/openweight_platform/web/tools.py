"""LangChain Core tool wrapper for the native web search service."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from openweight_platform.web.search import (
        TavilySearchService,
        WebSearchResult,
    )


class WebSearchInput(BaseModel):
    query: str = Field(description="Web search query.")
    max_results: int = Field(
        default=5,
        gt=0,
        description="Maximum number of search results to return.",
    )


class WebSearchToolResult(TypedDict):
    title: str
    url: str
    content: str
    score: float | None


def _structured_result(result: WebSearchResult) -> WebSearchToolResult:
    return {
        "title": result.title,
        "url": result.url,
        "content": result.content,
        "score": result.score,
    }


def build_web_search_tool(service: TavilySearchService) -> BaseTool:
    """Build a LangChain tool backed by the native Tavily search service."""

    def search_web(
        query: str,
        max_results: int = 5,
    ) -> list[WebSearchToolResult]:
        results = service.search(
            query=query,
            max_results=max_results,
        )
        return [_structured_result(result) for result in results]

    return StructuredTool.from_function(
        func=search_web,
        name="search_web",
        description=(
            "Search the external public web for current information, including "
            "recent events and facts that may change over time."
        ),
        args_schema=WebSearchInput,
    )


__all__ = [
    "WebSearchInput",
    "WebSearchToolResult",
    "build_web_search_tool",
]
