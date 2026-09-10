"""Portable Qwen vector index for the synthetic Hugging Face demo corpus."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
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
from openweight_platform.rag.embeddings import (
    DEFAULT_EMBEDDING_MODEL_ID,
    DEFAULT_QUERY_INSTRUCTION,
    EMBEDDING_DIMENSION,
    QwenEmbeddings,
)


PORTABLE_INDEX_VERSION = 1
DEMO_EMBEDDING_MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"


def _citation_id(document: Document) -> str:
    return f"[{document.metadata['policy_id']}#{document.metadata['chunk_index']}]"


def canonical_policy_chunks(
    policy_directory: str | Path = DEFAULT_POLICY_DIRECTORY,
) -> list[Document]:
    return split_policy_documents(load_policy_documents(policy_directory))


def _chunk_records(documents: list[Document]) -> list[dict[str, object]]:
    return [
        {
            "citation_id": _citation_id(document),
            "policy_id": document.metadata["policy_id"],
            "title": document.metadata["title"],
            "domain": document.metadata["domain"],
            "chunk_index": document.metadata["chunk_index"],
            "content_sha256": hashlib.sha256(
                document.page_content.encode("utf-8")
            ).hexdigest(),
        }
        for document in documents
    ]


def _corpus_digest(records: list[dict[str, object]]) -> str:
    payload = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_portable_policy_index(
    output_path: str | Path,
    *,
    policy_directory: str | Path = DEFAULT_POLICY_DIRECTORY,
    embeddings: QwenEmbeddings | None = None,
) -> dict[str, object]:
    """Build the reviewable manifest and compressed Qwen vector artifact."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    documents = canonical_policy_chunks(policy_directory)
    encoder = embeddings or QwenEmbeddings(device="cpu")
    vectors = np.asarray(
        encoder.embed_documents([item.page_content for item in documents]),
        dtype=np.float32,
    )
    if vectors.shape != (len(documents), EMBEDDING_DIMENSION):
        raise ValueError("Portable policy vectors have an unexpected shape")
    records = _chunk_records(documents)
    manifest: dict[str, object] = {
        "format_version": PORTABLE_INDEX_VERSION,
        "embedding_model_id": DEFAULT_EMBEDDING_MODEL_ID,
        "embedding_model_revision": DEMO_EMBEDDING_MODEL_REVISION,
        "embedding_dimension": encoder.dimension,
        "query_instruction": encoder.query_instruction,
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "chunk_overlap": DEFAULT_CHUNK_OVERLAP,
        "chunk_count": len(documents),
        "corpus_sha256": _corpus_digest(records),
        "chunks": records,
    }
    np.savez_compressed(output, vectors=vectors)
    output.with_suffix(".json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


class PortablePolicyStore:
    """Read a verified static corpus index and embed queries locally on CPU."""

    def __init__(
        self,
        index_path: str | Path,
        *,
        policy_directory: str | Path = DEFAULT_POLICY_DIRECTORY,
        embeddings: QwenEmbeddings | None = None,
    ) -> None:
        self._index_path = Path(index_path)
        self._policy_directory = Path(policy_directory)
        self._embeddings = embeddings
        self._documents: list[Document] | None = None
        self._vectors: np.ndarray[Any, np.dtype[np.float32]] | None = None

    def validate(self) -> dict[str, object]:
        documents = canonical_policy_chunks(self._policy_directory)
        manifest_path = self._index_path.with_suffix(".json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = _chunk_records(documents)
        expected = {
            "format_version": PORTABLE_INDEX_VERSION,
            "embedding_model_id": DEFAULT_EMBEDDING_MODEL_ID,
            "embedding_model_revision": DEMO_EMBEDDING_MODEL_REVISION,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "query_instruction": DEFAULT_QUERY_INSTRUCTION,
            "chunk_size": DEFAULT_CHUNK_SIZE,
            "chunk_overlap": DEFAULT_CHUNK_OVERLAP,
            "chunk_count": len(documents),
            "corpus_sha256": _corpus_digest(records),
            "chunks": records,
        }
        if manifest != expected:
            raise ValueError("Portable policy index does not match the canonical corpus")
        with np.load(self._index_path, allow_pickle=False) as archive:
            vectors = np.asarray(archive["vectors"], dtype=np.float32)
        if vectors.shape != (len(documents), EMBEDDING_DIMENSION):
            raise ValueError("Portable policy index has an unexpected vector shape")
        if not np.isfinite(vectors).all():
            raise ValueError("Portable policy index contains non-finite values")
        self._documents = documents
        self._vectors = vectors
        return manifest

    def similarity_search(self, query: str, k: int) -> list[Document]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if k <= 0:
            raise ValueError("k must be greater than zero")
        if self._vectors is None or self._documents is None:
            self.validate()
        if self._embeddings is None:
            self._embeddings = QwenEmbeddings(device="cpu")
        query_vector = np.asarray(
            self._embeddings.embed_query(query),
            dtype=np.float32,
        )
        if query_vector.shape != (EMBEDDING_DIMENSION,):
            raise ValueError("Portable policy query vector has an unexpected shape")
        vectors = self._vectors
        documents = self._documents
        if vectors is None or documents is None:
            raise RuntimeError("Portable policy index was not initialized")
        indexes = np.argsort(-(vectors @ query_vector), kind="stable")[:k]
        return [documents[int(index)] for index in indexes]


__all__ = [
    "DEMO_EMBEDDING_MODEL_REVISION",
    "PORTABLE_INDEX_VERSION",
    "PortablePolicyStore",
    "build_portable_policy_index",
    "canonical_policy_chunks",
]
