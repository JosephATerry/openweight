#!/usr/bin/env python3
"""Search the PostgreSQL-backed enterprise policy corpus."""

import argparse
import asyncio
from collections.abc import Sequence

from openweight_platform.rag.database import PostgresConfig
from openweight_platform.rag.embeddings import QwenEmbeddings
from openweight_platform.rag.retrieval import search_policy_corpus
from openweight_platform.rag.vectorstore import create_policy_vector_store


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Semantically search indexed enterprise policy chunks.",
    )
    parser.add_argument("query", help="Natural-language policy question.")
    parser.add_argument(
        "--top-k",
        type=int,
        default=4,
        help="Number of matching chunks to return.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = PostgresConfig.from_env()
    embeddings = QwenEmbeddings()
    engine = None

    try:
        engine, vector_store = create_policy_vector_store(
            config,
            embeddings,
        )
        results = search_policy_corpus(
            vector_store,
            args.query,
            k=args.top_k,
        )

        print(f"Query: {args.query}")
        print(f"Top-k: {args.top_k}")

        for rank, document in enumerate(results, start=1):
            metadata = document.metadata
            preview = " ".join(document.page_content.split())

            if len(preview) > 320:
                preview = f"{preview[:317]}..."

            print(f"\nRank: {rank}")
            print(f"Policy ID: {metadata['policy_id']}")
            print(f"Title: {metadata['title']}")
            print(f"Domain: {metadata['domain']}")
            print(f"Source: {metadata['source_path']}")
            print(f"Chunk index: {metadata['chunk_index']}")
            print(f"Content: {preview}")
    finally:
        if engine is not None:
            asyncio.run(engine.close())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
