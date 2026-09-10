from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openweight_platform.rag.embeddings import EMBEDDING_DIMENSION
from openweight_platform.rag.portable import PortablePolicyStore


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "deploy/huggingface/policy_index.npz"


class FakeQueryEmbeddings:
    def embed_query(self, _: str) -> list[float]:
        return np.full(
            EMBEDDING_DIMENSION,
            1 / np.sqrt(EMBEDDING_DIMENSION),
            dtype=np.float32,
        ).tolist()


def test_committed_index_matches_only_the_canonical_policy_corpus() -> None:
    manifest = PortablePolicyStore(INDEX).validate()

    assert manifest["embedding_model_id"] == "Qwen/Qwen3-Embedding-0.6B"
    assert manifest["embedding_model_revision"] == (
        "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    )
    assert manifest["embedding_dimension"] == 1024
    assert manifest["chunk_count"] == 49
    assert len(manifest["chunks"]) == 49
    assert all(
        str(item["citation_id"]).startswith("[EPG-")
        for item in manifest["chunks"]
    )


def test_portable_search_reuses_canonical_documents_without_loading_a_model() -> None:
    store = PortablePolicyStore(INDEX, embeddings=FakeQueryEmbeddings())

    documents = store.similarity_search("What controls access?", 3)

    assert len(documents) == 3
    assert all("policy_id" in document.metadata for document in documents)
    assert all("chunk_index" in document.metadata for document in documents)


def test_tampered_manifest_fails_closed(tmp_path: Path) -> None:
    copied_index = tmp_path / "policy_index.npz"
    copied_manifest = copied_index.with_suffix(".json")
    copied_index.write_bytes(INDEX.read_bytes())
    manifest = json.loads(INDEX.with_suffix(".json").read_text(encoding="utf-8"))
    manifest["chunk_count"] = 48
    copied_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        PortablePolicyStore(copied_index).validate()
