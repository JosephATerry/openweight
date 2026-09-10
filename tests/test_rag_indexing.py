from langchain_core.documents import Document

from openweight_platform.rag import indexing


class FakeDocumentStore:
    def __init__(self):
        self.documents = None
        self.ids = None

    def add_documents(self, documents, ids=None):
        self.documents = documents
        self.ids = ids
        return ids


def make_chunk(policy_id="EPG-TEST-001", chunk_index=0):
    return Document(
        page_content="Synthetic policy content",
        metadata={
            "policy_id": policy_id,
            "title": "Synthetic Policy",
            "domain": "testing",
            "source_path": "data/policies/test.md",
            "chunk_index": chunk_index,
        },
    )


def test_policy_chunk_ids_are_deterministic_and_distinct():
    first = make_chunk(chunk_index=0)
    same = make_chunk(chunk_index=0)
    different_chunk = make_chunk(chunk_index=1)
    different_policy = make_chunk(policy_id="EPG-TEST-002", chunk_index=0)

    assert indexing.policy_chunk_id(first) == indexing.policy_chunk_id(same)
    assert indexing.policy_chunk_id(first) != indexing.policy_chunk_id(
        different_chunk
    )
    assert indexing.policy_chunk_id(first) != indexing.policy_chunk_id(
        different_policy
    )


def test_ingestion_uses_existing_document_and_chunk_pipeline(monkeypatch):
    source_documents = [make_chunk()]
    chunks = [make_chunk(chunk_index=0), make_chunk(chunk_index=1)]
    calls = []

    def fake_load(path):
        calls.append(("load", path))
        return source_documents

    def fake_split(documents, *, chunk_size, chunk_overlap):
        calls.append(("split", documents, chunk_size, chunk_overlap))
        return chunks

    monkeypatch.setattr(indexing, "load_policy_documents", fake_load)
    monkeypatch.setattr(indexing, "split_policy_documents", fake_split)
    store = FakeDocumentStore()

    result = indexing.index_policy_corpus(
        store,
        "policy-directory",
        chunk_size=400,
        chunk_overlap=50,
    )

    assert calls == [
        ("load", "policy-directory"),
        ("split", source_documents, 400, 50),
    ]
    assert store.documents == chunks
    assert store.ids == [indexing.policy_chunk_id(chunk) for chunk in chunks]
    assert result.document_count == 1
    assert result.chunk_count == 2
    assert result.row_ids == store.ids
