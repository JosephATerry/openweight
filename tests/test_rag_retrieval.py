from langchain_core.documents import Document

from openweight_platform.rag.retrieval import search_policy_corpus


class FakeSemanticStore:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def similarity_search(self, query, k):
        self.calls.append((query, k))
        return self.documents[:k]


def test_semantic_retrieval_returns_documents_with_metadata():
    document = Document(
        page_content="Privileged access requires MFA.",
        metadata={
            "policy_id": "EPG-ACCESS-001",
            "title": "Privileged Infrastructure Access Policy",
            "domain": "infrastructure-access",
            "source_path": "data/policies/01_privileged.md",
            "chunk_index": 2,
        },
    )
    store = FakeSemanticStore([document])

    results = search_policy_corpus(
        store,
        "What controls apply to production administrators?",
        k=3,
    )

    assert store.calls == [
        ("What controls apply to production administrators?", 3)
    ]
    assert results == [document]
    assert results[0].metadata["policy_id"] == "EPG-ACCESS-001"
    assert results[0].metadata["chunk_index"] == 2
