"""Load, chunk, and persist the enterprise policy corpus."""

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from langchain_core.documents import Document

from openweight_platform.rag.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    split_policy_documents,
)
from openweight_platform.rag.documents import (
    DEFAULT_POLICY_DIRECTORY,
    load_policy_documents,
)


POLICY_CHUNK_NAMESPACE = uuid.UUID("8426944d-3790-55af-92ce-bfde1747308b")


class DocumentStore(Protocol):
    def add_documents(
        self,
        documents: list[Document],
        ids: list[str] | None = None,
    ) -> list[str]: ...


@dataclass(frozen=True)
class IndexingResult:
    document_count: int
    chunk_count: int
    row_ids: list[str]


def policy_chunk_id(document: Document) -> str:
    """Build a stable UUID from a chunk's policy identity and index."""

    policy_id = document.metadata["policy_id"]
    chunk_index = document.metadata["chunk_index"]
    identity = f"{policy_id}:{chunk_index}"
    return str(uuid.uuid5(POLICY_CHUNK_NAMESPACE, identity))


def build_policy_chunks(
    policy_directory: str | Path = DEFAULT_POLICY_DIRECTORY,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[list[Document], list[Document]]:
    """Run the existing policy loading and chunking pipeline."""

    documents = load_policy_documents(policy_directory)
    chunks = split_policy_documents(
        documents,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return documents, chunks


def index_policy_corpus(
    vector_store: DocumentStore,
    policy_directory: str | Path = DEFAULT_POLICY_DIRECTORY,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> IndexingResult:
    """Embed and upsert the policy corpus using deterministic chunk IDs."""

    documents, chunks = build_policy_chunks(
        policy_directory,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    ids = [policy_chunk_id(chunk) for chunk in chunks]
    row_ids = vector_store.add_documents(chunks, ids=ids)

    return IndexingResult(
        document_count=len(documents),
        chunk_count=len(chunks),
        row_ids=row_ids,
    )
