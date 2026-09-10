from importlib import import_module

import pytest

from openweight_platform.web.search import (
    TavilySearchService,
    WebSearchResult,
)


search_module = import_module("openweight_platform.web.search")


class FakeTavilyClient:
    def __init__(self, response):
        self.response = response
        self.search_calls = []

    def search(self, *, query, max_results):
        self.search_calls.append(
            {
                "query": query,
                "max_results": max_results,
            }
        )
        return self.response


def test_non_empty_query_uses_default_result_limit():
    client = FakeTavilyClient({"results": []})
    service = TavilySearchService(client)

    assert service.search("fictional enterprise news") == []
    assert client.search_calls == [
        {
            "query": "fictional enterprise news",
            "max_results": 5,
        }
    ]


@pytest.mark.parametrize("query", ["", "   ", None])
def test_empty_or_non_string_query_is_rejected(query):
    client = FakeTavilyClient({"results": []})
    service = TavilySearchService(client)

    with pytest.raises(ValueError, match="non-empty string"):
        service.search(query)

    assert client.search_calls == []


@pytest.mark.parametrize("max_results", [0, -1, True, 1.5])
def test_invalid_max_results_is_rejected(max_results):
    client = FakeTavilyClient({"results": []})
    service = TavilySearchService(client)

    with pytest.raises(ValueError, match="positive integer"):
        service.search("fictional query", max_results=max_results)

    assert client.search_calls == []


def test_client_receives_expected_query_and_result_limit():
    client = FakeTavilyClient({"results": []})
    service = TavilySearchService(client)

    service.search("exact search terms", max_results=3)

    assert client.search_calls == [
        {
            "query": "exact search terms",
            "max_results": 3,
        }
    ]


def test_provider_results_become_structured_objects():
    client = FakeTavilyClient(
        {
            "results": [
                {
                    "title": "Fictional Example Result",
                    "url": "https://example.invalid/result",
                    "content": "A fictional search-result summary.",
                    "score": 0.875,
                }
            ]
        }
    )
    service = TavilySearchService(client)

    results = service.search("fictional result")

    assert results == [
        WebSearchResult(
            title="Fictional Example Result",
            url="https://example.invalid/result",
            content="A fictional search-result summary.",
            score=0.875,
        )
    ]


def test_optional_score_is_normalized_or_omitted_cleanly():
    client = FakeTavilyClient(
        {
            "results": [
                {
                    "title": "Integer Score",
                    "url": "https://example.invalid/integer",
                    "content": "Score becomes a float.",
                    "score": 1,
                },
                {
                    "title": "No Score",
                    "url": "https://example.invalid/no-score",
                    "content": "Score is absent.",
                },
            ]
        }
    )
    service = TavilySearchService(client)

    results = service.search("score handling")

    assert results[0].score == 1.0
    assert results[1].score is None


def test_missing_optional_fields_are_handled_cleanly():
    client = FakeTavilyClient(
        {
            "results": [
                {
                    "title": "Sparse Fictional Result",
                    "url": "https://example.invalid/sparse",
                }
            ]
        }
    )
    service = TavilySearchService(client)

    results = service.search("sparse result")

    assert results == [
        WebSearchResult(
            title="Sparse Fictional Result",
            url="https://example.invalid/sparse",
            content="",
            score=None,
        )
    ]


def test_returned_objects_do_not_include_api_key_information():
    api_key = "tvly-unit-test-secret"
    client = FakeTavilyClient(
        {
            "api_key": api_key,
            "results": [
                {
                    "title": "Safe Result",
                    "url": "https://example.invalid/safe",
                    "content": "Public content only.",
                    "score": 0.5,
                    "api_key": api_key,
                }
            ],
        }
    )
    service = TavilySearchService(client)

    results = service.search("safe result")

    assert api_key not in repr(results)
    assert not hasattr(results[0], "api_key")


def test_injected_client_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    client = FakeTavilyClient({"results": []})

    service = TavilySearchService(client)

    assert service.search("offline test") == []


def test_missing_api_key_is_reported_without_revealing_a_value():
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY") as error:
        TavilySearchService(environ={})

    assert "tvly-" not in str(error.value)


def test_environment_api_key_is_passed_only_to_client(monkeypatch):
    created_clients = []

    class FakeEnvironmentClient(FakeTavilyClient):
        def __init__(self, *, api_key):
            super().__init__({"results": []})
            self.api_key = api_key
            created_clients.append(self)

    monkeypatch.setattr(search_module, "TavilyClient", FakeEnvironmentClient)

    service = TavilySearchService(
        environ={"TAVILY_API_KEY": "tvly-environment-secret"}
    )
    results = service.search("environment configuration")

    assert created_clients[0].api_key == "tvly-environment-secret"
    assert "tvly-environment-secret" not in repr(results)
