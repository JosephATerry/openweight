#!/usr/bin/env python3
"""Inspect policy documents and their retrieval chunks."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from openweight_platform.rag.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    split_policy_documents,
)
from openweight_platform.rag.documents import (
    DEFAULT_POLICY_DIRECTORY,
    load_policy_documents,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and inspect synthetic enterprise policy chunks.",
    )
    parser.add_argument(
        "--policy-directory",
        type=Path,
        default=DEFAULT_POLICY_DIRECTORY,
        help="Directory containing Markdown policy documents.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Maximum chunk length in characters.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Character overlap between adjacent chunks.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=3,
        help="Number of chunks to display.",
    )

    return parser.parse_args(argv)


def _sample_chunks(chunks, sample_count):
    if sample_count <= 0 or not chunks:
        return []

    count = min(sample_count, len(chunks))
    indexes = [index * len(chunks) // count for index in range(count)]
    return [chunks[index] for index in indexes]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    documents = load_policy_documents(args.policy_directory)
    chunks = split_policy_documents(
        documents,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )

    print(f"Policy documents: {len(documents)}")
    print(f"Policy chunks: {len(chunks)}")
    print(f"Chunk size: {args.chunk_size} characters")
    print(f"Chunk overlap: {args.chunk_overlap} characters")

    for sample_number, chunk in enumerate(
        _sample_chunks(chunks, args.samples),
        start=1,
    ):
        preview = chunk.page_content.replace("\n", " ")
        if len(preview) > 240:
            preview = f"{preview[:237]}..."

        print(f"\nSample {sample_number}")
        print(f"Metadata: {chunk.metadata}")
        print(f"Characters: {len(chunk.page_content)}")
        print(f"Content: {preview}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
