from importlib import import_module
from pathlib import Path

from langchain_core.tools import BaseTool

from openweight_platform.web.search import WebSearchResult
from openweight_platform.web.tools import build_web_search_tool


class FakeSearchService:
    def __init__(self, results):
        self.results = results
        self.search_calls = []

    def search(self, query, max_results=5):
        self.search_calls.append(
            {
                "query": query,
                "max_results": max_results,
            }
        )
        return self.results


def test_factory_returns_langchain_base_tool_with_expected_name():
    tool = build_web_search_tool(FakeSearchService([]))

    assert isinstance(tool, BaseTool)
    assert tool.name == "search_web"


def test_tool_has_typed_input_schema_and_default_result_limit():
    tool = build_web_search_tool(FakeSearchService([]))

    schema = tool.args_schema.model_json_schema()

    assert schema["required"] == ["query"]
    assert schema["properties"]["query"]["type"] == "string"
    assert schema["properties"]["max_results"]["type"] == "integer"
    assert schema["properties"]["max_results"]["default"] == 5


def test_description_identifies_current_external_public_web_use():
    tool = build_web_search_tool(FakeSearchService([]))
    description = tool.description.lower()

    assert "current" in description
    assert "external" in description
    assert "public web" in description


def test_invocation_forwards_query_and_result_limit_to_service():
    service = FakeSearchService([])
    tool = build_web_search_tool(service)

    result = tool.invoke(
        {
            "query": "current fictional market update",
            "max_results": 3,
        }
    )

    assert result == []
    assert service.search_calls == [
        {
            "query": "current fictional market update",
            "max_results": 3,
        }
    ]


def test_invocation_uses_default_result_limit():
    service = FakeSearchService([])
    tool = build_web_search_tool(service)

    tool.invoke({"query": "current fictional news"})

    assert service.search_calls == [
        {
            "query": "current fictional news",
            "max_results": 5,
        }
    ]


def test_structured_results_are_preserved_as_json_compatible_values():
    service = FakeSearchService(
        [
            WebSearchResult(
                title="Fictional Public Update",
                url="https://example.invalid/public-update",
                content="A fictional current-information summary.",
                score=0.91,
            ),
            WebSearchResult(
                title="Fictional Result Without Score",
                url="https://example.invalid/no-score",
                content="Another fictional summary.",
                score=None,
            ),
        ]
    )
    tool = build_web_search_tool(service)

    result = tool.invoke({"query": "fictional public update"})

    assert result == [
        {
            "title": "Fictional Public Update",
            "url": "https://example.invalid/public-update",
            "content": "A fictional current-information summary.",
            "score": 0.91,
        },
        {
            "title": "Fictional Result Without Score",
            "url": "https://example.invalid/no-score",
            "content": "Another fictional summary.",
            "score": None,
        },
    ]


def test_empty_search_results_are_returned_as_empty_list():
    tool = build_web_search_tool(FakeSearchService([]))

    assert tool.invoke({"query": "no fictional matches"}) == []


def test_wrapper_contains_no_tavily_client_or_api_key_logic():
    tools_module = import_module("openweight_platform.web.tools")
    source = Path(tools_module.__file__).read_text(encoding="utf-8")

    assert "TavilyClient" not in source
    assert "TAVILY_API_KEY" not in source
