"""Structured web search backed by the Tavily Python SDK."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from tavily import TavilyClient


TAVILY_API_KEY_VARIABLE = "TAVILY_API_KEY"
DEFAULT_MAX_RESULTS = 5


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    content: str
    score: float | None


class TavilySearchClient(Protocol):
    def search(self, *, query: str, max_results: int) -> dict[str, Any]: ...


class TavilySearchService:
    """Validate searches and convert Tavily responses into structured results."""

    def __init__(
        self,
        client: TavilySearchClient | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return

        values = os.environ if environ is None else environ
        api_key = values.get(TAVILY_API_KEY_VARIABLE)

        if not api_key:
            raise RuntimeError(
                f"Missing required environment variable: {TAVILY_API_KEY_VARIABLE}"
            )

        self._client = TavilyClient(api_key=api_key)

    def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> list[WebSearchResult]:
        """Search Tavily and return normalized structured result objects."""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")

        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or max_results <= 0
        ):
            raise ValueError("max_results must be a positive integer")

        response = self._client.search(
            query=query,
            max_results=max_results,
        )
        provider_results = response.get("results", [])

        if not isinstance(provider_results, list):
            return []

        results = []
        for provider_result in provider_results:
            if not isinstance(provider_result, Mapping):
                continue

            raw_score = provider_result.get("score")
            score = (
                float(raw_score)
                if isinstance(raw_score, (int, float))
                and not isinstance(raw_score, bool)
                else None
            )
            results.append(
                WebSearchResult(
                    title=_string_value(provider_result.get("title")),
                    url=_string_value(provider_result.get("url")),
                    content=_string_value(provider_result.get("content")),
                    score=score,
                )
            )

        return results


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[WebSearchResult]:
    """Search using a Tavily client configured from the environment."""

    return TavilySearchService().search(query, max_results)


__all__ = [
    "DEFAULT_MAX_RESULTS",
    "TAVILY_API_KEY_VARIABLE",
    "TavilySearchService",
    "WebSearchResult",
    "search",
]
