#!/usr/bin/env python3
"""Answer one enterprise policy question with retrieved evidence."""

import argparse
import asyncio
from collections.abc import Sequence

from openweight_platform.backends.base import ModelBackend
from openweight_platform.backends.factory import (
    DEFAULT_BACKEND_NAME,
    DEFAULT_GPT_OSS_MODEL_ID,
    ModelBackendConfig,
    SUPPORTED_BACKENDS,
    build_model_backend,
)
from openweight_platform.backends.muse_glimmer import (
    DEFAULT_MUSE_BASE_URL,
    DEFAULT_MUSE_MODEL_ALIAS,
    DEFAULT_MUSE_TIMEOUT_SECONDS,
    SUPPORTED_REASONING_STRENGTHS,
)
from openweight_platform.rag.database import PostgresConfig
from openweight_platform.rag.embeddings import QwenEmbeddings
from openweight_platform.rag.generation import generate_grounded_answer
from openweight_platform.rag.vectorstore import create_policy_vector_store


DEFAULT_MODEL_ID = DEFAULT_GPT_OSS_MODEL_ID
DEFAULT_MAX_NEW_TOKENS = 2048


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Answer a policy question using retrieved evidence.",
    )
    parser.add_argument(
        "--backend",
        choices=SUPPORTED_BACKENDS,
        default=DEFAULT_BACKEND_NAME,
        help="Local model backend (default: gpt-oss).",
    )
    parser.add_argument("question", help="Natural-language policy question.")
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of policy chunks to supply as evidence.",
    )
    parser.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        help="Hugging Face model ID or local model path for generation.",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=DEFAULT_MAX_NEW_TOKENS,
        help="Maximum number of tokens generated for the grounded answer.",
    )
    parser.add_argument(
        "--muse-base-url",
        default=DEFAULT_MUSE_BASE_URL,
        help="Local llama.cpp OpenAI-compatible /v1 base URL.",
    )
    parser.add_argument(
        "--muse-model-alias",
        default=DEFAULT_MUSE_MODEL_ALIAS,
        help="Model alias sent to the local llama.cpp server.",
    )
    parser.add_argument(
        "--muse-timeout-seconds",
        type=float,
        default=DEFAULT_MUSE_TIMEOUT_SECONDS,
        help="Timeout for one local Muse generation request.",
    )
    parser.add_argument(
        "--reasoning-strength",
        choices=SUPPORTED_REASONING_STRENGTHS,
        default="low",
        help="Muse reasoning effort (default: low).",
    )
    return parser.parse_args(argv)


def _create_backend(args: argparse.Namespace) -> ModelBackend:
    return build_model_backend(
        ModelBackendConfig(
            backend_name=args.backend,
            max_new_tokens=args.max_new_tokens,
            gpt_oss_model_id=args.model_id,
            muse_base_url=args.muse_base_url,
            muse_model_alias=args.muse_model_alias,
            muse_timeout_seconds=args.muse_timeout_seconds,
            reasoning_strength=args.reasoning_strength,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.top_k <= 0:
        raise ValueError("top-k must be greater than zero")

    if args.max_new_tokens <= 0:
        raise ValueError("max-new-tokens must be greater than zero")

    config = PostgresConfig.from_env()
    embeddings = QwenEmbeddings()
    engine = None
    backend = None

    try:
        engine, vector_store = create_policy_vector_store(
            config,
            embeddings,
        )

        backend = _create_backend(args)
        backend.load()
        result = generate_grounded_answer(
            args.question,
            vector_store,
            backend,
            top_k=args.top_k,
        )

        print("Question:")
        print(result.question)
        print("\nAnswer:")
        print(result.answer)
        print("\nRetrieved sources:")

        for source in result.retrieved_sources:
            preview = " ".join(source.content.split())

            if len(preview) > 240:
                preview = f"{preview[:237]}..."

            print(
                f"- {source.citation_id} {source.title} "
                f"(domain={source.domain}, source={source.source_path}, "
                f"chunk={source.chunk_index})"
            )
            print(f"  {preview}")

        citations = ", ".join(result.cited_source_ids) or "<none>"
        print(f"\nCitations used: {citations}")
        print(
            "Citation validity: "
            f"{'valid' if result.citation_valid else 'invalid'}"
        )
        print(f"Generation seconds: {result.generation_seconds:.3f}")
        print(f"Input tokens: {result.input_tokens}")
        print(f"Output tokens: {result.output_tokens}")
        print(f"Peak VRAM: {result.peak_vram_gib:.2f} GiB")
    finally:
        if backend is not None:
            backend.unload()

        if engine is not None:
            asyncio.run(engine.close())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
