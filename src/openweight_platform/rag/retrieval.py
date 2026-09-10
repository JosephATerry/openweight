"""Semantic retrieval over the indexed policy corpus."""

from typing import Protocol

from langchain_core.documents import Document


class SemanticStore(Protocol):
    def similarity_search(self, query: str, k: int) -> list[Document]: ...


def search_policy_corpus(
    vector_store: SemanticStore,
    query: str,
    *,
    k: int = 4,
) -> list[Document]:
    """Return the top policy chunks for a natural-language query."""

    if not query.strip():
        raise ValueError("query must not be empty")

    if k <= 0:
        raise ValueError("k must be greater than zero")

    return vector_store.similarity_search(query, k=k)
