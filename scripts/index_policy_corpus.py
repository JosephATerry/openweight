#!/usr/bin/env python3
"""Embed and persist the synthetic policy corpus in PostgreSQL."""

import asyncio
import os
import time

from openweight_platform.rag.database import PostgresConfig
from openweight_platform.rag.embeddings import (
    DEFAULT_EMBEDDING_MODEL_ID,
    QwenEmbeddings,
)
from openweight_platform.rag.indexing import index_policy_corpus
from openweight_platform.rag.vectorstore import (
    count_policy_chunks,
    create_policy_vector_store,
)


def main() -> int:
    started_at = time.perf_counter()
    config = PostgresConfig.from_env()
    embeddings = QwenEmbeddings(
        model_id=os.environ.get(
            "OPENWEIGHT_DEMO_EMBEDDING_MODEL",
            DEFAULT_EMBEDDING_MODEL_ID,
        )
    )
    engine = None

    try:
        engine, vector_store = create_policy_vector_store(
            config,
            embeddings,
            initialize=True,
        )
        result = index_policy_corpus(vector_store)
        row_count = count_policy_chunks(config)
        elapsed = time.perf_counter() - started_at

        print(f"Model ID: {embeddings.model_id}")
        print(f"Embedding dimension: {embeddings.dimension}")
        print(f"Device: {embeddings.device}")
        print(f"Policy documents: {result.document_count}")
        print(f"Policy chunks: {result.chunk_count}")
        print(f"Rows indexed: {len(result.row_ids)}")
        print(f"Database rows: {row_count}")
        print(f"Elapsed seconds: {elapsed:.2f}")
    finally:
        if engine is not None:
            asyncio.run(engine.close())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
