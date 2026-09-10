from collections import defaultdict
from pathlib import Path

from langchain_core.documents import Document

from openweight_platform.rag.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    split_policy_documents,
)
from openweight_platform.rag.documents import load_policy_documents


POLICY_DIRECTORY = Path("data/policies")
REQUIRED_METADATA = {
    "policy_id",
    "title",
    "domain",
    "source_path",
}


def test_all_policy_documents_load_with_unique_ids_and_metadata():
    documents = load_policy_documents(POLICY_DIRECTORY)

    assert len(documents) == 12
    assert all(isinstance(document, Document) for document in documents)
    assert len({document.metadata["policy_id"] for document in documents}) == 12

    for document in documents:
        assert REQUIRED_METADATA <= document.metadata.keys()
        assert all(document.metadata[key] for key in REQUIRED_METADATA)
        assert document.page_content.strip()
        assert document.metadata["source_path"].startswith("data/policies/")


def test_default_chunking_is_non_empty_bounded_and_preserves_metadata():
    documents = load_policy_documents(POLICY_DIRECTORY)
    chunks = split_policy_documents(documents)

    assert len(chunks) > len(documents)

    original_metadata = {
        document.metadata["policy_id"]: document.metadata
        for document in documents
    }
    indexes_by_policy = defaultdict(list)

    for chunk in chunks:
        assert chunk.page_content.strip()
        assert len(chunk.page_content) <= DEFAULT_CHUNK_SIZE
        policy_id = chunk.metadata["policy_id"]
        indexes_by_policy[policy_id].append(chunk.metadata["chunk_index"])

        for key, value in original_metadata[policy_id].items():
            assert chunk.metadata[key] == value

    for indexes in indexes_by_policy.values():
        assert indexes == list(range(len(indexes)))


def test_chunk_size_and_overlap_are_configurable():
    document = Document(
        page_content="A short paragraph. " * 80,
        metadata={
            "policy_id": "TEST-001",
            "title": "Test Policy",
            "domain": "testing",
            "source_path": "test.md",
        },
    )

    chunks = split_policy_documents(
        [document],
        chunk_size=240,
        chunk_overlap=40,
    )

    assert len(chunks) > 1
    assert all(len(chunk.page_content) <= 240 for chunk in chunks)
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(
        range(len(chunks))
    )
    assert DEFAULT_CHUNK_OVERLAP == 120
